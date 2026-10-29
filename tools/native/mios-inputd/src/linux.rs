// AI-hint: Linux evdev/uinput file-descriptor ownership, capability cloning and private fixture gate.
// AI-related: docs.kernel.org/input/uinput.html, docs.kernel.org/input/event-codes.html
use crate::config::VIRTUAL_PREFIX;
use crate::router::{Axis, Event, EV_ABS, EV_KEY, EV_REL, EV_SYN, SYN_REPORT};
use std::collections::{BTreeMap, BTreeSet};
use std::fs::{File, OpenOptions};
use std::io::{self, Read, Write};
use std::os::fd::{AsRawFd, RawFd};
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};
const fn ioc(dir: u64, ty: u8, n: u8, size: usize) -> libc::c_ulong {
    ((dir << 30) | ((size as u64) << 16) | ((ty as u64) << 8) | n as u64) as libc::c_ulong
}
const fn read_ioctl(n: u8, size: usize) -> libc::c_ulong {
    ioc(2, b'E', n, size)
}
#[repr(C)]
#[derive(Clone, Copy, Default)]
struct InputId {
    bustype: u16,
    vendor: u16,
    product: u16,
    version: u16,
}
#[repr(C)]
#[derive(Clone, Copy, Default)]
struct AbsInfo {
    value: i32,
    minimum: i32,
    maximum: i32,
    fuzz: i32,
    flat: i32,
    resolution: i32,
}
#[repr(C)]
#[derive(Clone, Copy)]
struct InputEvent {
    time: libc::timeval,
    kind: u16,
    code: u16,
    value: i32,
}
#[repr(C)]
struct UinputSetup {
    id: InputId,
    name: [u8; 80],
    ff_effects_max: u32,
}
#[repr(C)]
struct UinputAbsSetup {
    code: u16,
    padding: u16,
    info: AbsInfo,
}
fn ioctl_ptr<T>(fd: RawFd, request: libc::c_ulong, v: &mut T) -> io::Result<()> {
    // SAFETY: every caller supplies the Linux UAPI's exact initialized repr(C)
    // type/buffer; ioctl completes synchronously and retains no pointer.
    if unsafe { libc::ioctl(fd, request as _, v as *mut T) } < 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(())
    }
}
fn ioctl_int(fd: RawFd, request: libc::c_ulong, value: i32) -> io::Result<()> {
    // SAFETY: these requests take an integer, not a user pointer.
    if unsafe { libc::ioctl(fd, request as _, value) } < 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(())
    }
}
fn bitset(fd: RawFd, event: u8) -> io::Result<[u8; 128]> {
    let mut b = [0u8; 128];
    ioctl_ptr(fd, read_ioctl(0x20 + event, b.len()), &mut b)?;
    Ok(b)
}
fn codes(bits: &[u8]) -> Vec<u16> {
    (0..bits.len() * 8)
        .filter(|n| bits[n / 8] & (1 << (n % 8)) != 0)
        .map(|n| n as u16)
        .collect()
}
fn name_of(fd: RawFd) -> io::Result<String> {
    let mut b = [0u8; 256];
    ioctl_ptr(fd, read_ioctl(0x06, b.len()), &mut b)?;
    let end = b.iter().position(|c| *c == 0).unwrap_or(b.len());
    Ok(String::from_utf8_lossy(&b[..end]).into())
}
pub struct Device {
    pub file: File,
    pub path: PathBuf,
    pub name: String,
    pub axes: BTreeMap<u16, Axis>,
    keys: Vec<u16>,
    abs: BTreeMap<u16, AbsInfo>,
    id: InputId,
    grabbed: bool,
}
impl Device {
    pub fn open(path: &Path, fixture_only: bool) -> io::Result<Self> {
        let file = OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_NONBLOCK | libc::O_CLOEXEC | libc::O_NOFOLLOW)
            .open(path)?;
        let fd = file.as_raw_fd();
        let name = name_of(fd)?;
        if name.starts_with(VIRTUAL_PREFIX) {
            return Err(io::Error::other(
                "refusing virtual output device: replay loop",
            ));
        }
        if !fixture_only && name.starts_with("mios-input-fixture-") {
            return Err(io::Error::other("private test fixture is isolated from the live bridge"));
        }
        if fixture_only && !name.starts_with("mios-input-fixture-") {
            return Err(io::Error::other("fixture gate refuses a physical device"));
        }
        let keys = codes(&bitset(fd, EV_KEY as u8)?);
        let ac = codes(&bitset(fd, EV_ABS as u8)?);
        if [0x1c, 0x1e, 0x2c].iter().all(|code| keys.contains(code)) {
            return Err(io::Error::other(
                "typing keyboard capabilities: controller bridge must not claim keyboards",
            ));
        }
        if !keys.contains(&0x130) || !ac.contains(&0) || !ac.contains(&1) {
            return Err(io::Error::other(
                "not a gamepad (requires BTN_SOUTH, ABS_X/Y)",
            ));
        }
        let mut axes = BTreeMap::new();
        let mut abs = BTreeMap::new();
        for code in ac {
            let mut info = AbsInfo::default();
            ioctl_ptr(
                fd,
                read_ioctl(0x40 + code as u8, std::mem::size_of::<AbsInfo>()),
                &mut info,
            )?;
            axes.insert(
                code,
                Axis {
                    min: info.minimum,
                    max: info.maximum,
                    value: info.value,
                },
            );
            abs.insert(code, info);
        }
        let mut id = InputId::default();
        ioctl_ptr(
            fd,
            read_ioctl(0x02, std::mem::size_of::<InputId>()),
            &mut id,
        )?;
        // This keyboard's auxiliary HID interface advertises a joystick but
        // no typing keys. Preserve the legacy daemon's hardware exclusion;
        // evdev shape and ID_INPUT_JOYSTICK alone cannot distinguish it.
        if id.vendor == 0x04d9 && id.product == 0xa293 {
            return Err(io::Error::other(
                "keyboard HID quirk: OBINS auxiliary joystick is not a controller",
            ));
        }
        Ok(Self {
            file,
            path: path.into(),
            name,
            axes,
            keys,
            abs,
            id,
            grabbed: false,
        })
    }
    pub fn grab(&mut self) -> io::Result<()> {
        ioctl_int(self.file.as_raw_fd(), ioc(1, b'E', 0x90, 4), 1)?;
        self.grabbed = true;
        Ok(())
    }
    pub fn release(&mut self) {
        if self.grabbed {
            let _ = ioctl_int(self.file.as_raw_fd(), ioc(1, b'E', 0x90, 4), 0);
            self.grabbed = false;
        }
    }
    pub fn held_keys(&self) -> io::Result<BTreeSet<u16>> {
        let mut bits = [0u8; 128];
        ioctl_ptr(self.file.as_raw_fd(), read_ioctl(0x18, bits.len()), &mut bits)?;
        Ok(codes(&bits).into_iter().collect())
    }
    pub fn snapshot(&self) -> io::Result<(BTreeSet<u16>, BTreeMap<u16, Axis>)> {
        let fd = self.file.as_raw_fd();
        let keys = self.held_keys()?;
        let mut axes = BTreeMap::new();
        for code in self.axes.keys() {
            let mut info = AbsInfo::default();
            ioctl_ptr(
                fd,
                read_ioctl(0x40 + *code as u8, std::mem::size_of::<AbsInfo>()),
                &mut info,
            )?;
            axes.insert(
                *code,
                Axis {
                    min: info.minimum,
                    max: info.maximum,
                    value: info.value,
                },
            );
        }
        Ok((keys, axes))
    }
    pub fn read_events(&mut self) -> io::Result<Vec<Event>> {
        // A byte buffer avoids alignment and uninitialized-value assumptions.
        let size = std::mem::size_of::<InputEvent>();
        let mut bytes = vec![0u8; size * 64];
        let len = match self.file.read(&mut bytes) {
            Err(e) if e.kind() == io::ErrorKind::WouldBlock => return Ok(vec![]),
            r => r?,
        };
        if len == 0 {
            return Err(io::Error::new(
                io::ErrorKind::UnexpectedEof,
                "input device removed",
            ));
        }
        if len % size != 0 {
            return Err(io::Error::other("partial input_event"));
        }
        let mut out = vec![];
        for bytes in bytes[..len].chunks_exact(size) {
            // SAFETY: input_event is plain integers/timeval and read_unaligned
            // copies exactly one complete initialized kernel event.
            let e = unsafe { std::ptr::read_unaligned(bytes.as_ptr().cast::<InputEvent>()) };
            out.push(Event::new(e.kind, e.code, e.value));
        }
        Ok(out)
    }
}
impl Drop for Device {
    fn drop(&mut self) {
        self.release();
    }
}
pub struct VirtualDevice {
    file: File,
    pub name: String,
}
impl VirtualDevice {
    fn create(
        name: &str,
        id: InputId,
        keys: &[u16],
        abs: &BTreeMap<u16, AbsInfo>,
        rel: &[u16],
    ) -> io::Result<Self> {
        if name.len() >= 80 || name.as_bytes().contains(&0) {
            return Err(io::Error::other("invalid virtual device name"));
        }
        let file = OpenOptions::new()
            .write(true)
            .custom_flags(libc::O_NONBLOCK | libc::O_CLOEXEC)
            .open("/dev/uinput")?;
        let fd = file.as_raw_fd();
        for (kind, items) in [
            (EV_KEY, keys.to_vec()),
            (EV_ABS, abs.keys().copied().collect()),
            (EV_REL, rel.to_vec()),
        ] {
            if items.is_empty() {
                continue;
            }
            ioctl_int(fd, ioc(1, b'U', 100, 4), i32::from(kind))?;
            let nr = match kind {
                EV_KEY => 101,
                EV_REL => 102,
                EV_ABS => 103,
                _ => unreachable!(),
            };
            for code in items {
                ioctl_int(fd, ioc(1, b'U', nr, 4), i32::from(code))?;
            }
        }
        for (code, info) in abs {
            let mut a = UinputAbsSetup {
                code: *code,
                padding: 0,
                info: *info,
            };
            ioctl_ptr(
                fd,
                ioc(1, b'U', 4, std::mem::size_of::<UinputAbsSetup>()),
                &mut a,
            )?;
        }
        let mut setup = UinputSetup {
            id,
            name: [0; 80],
            ff_effects_max: 0,
        };
        setup.name[..name.len()].copy_from_slice(name.as_bytes());
        ioctl_ptr(
            fd,
            ioc(1, b'U', 3, std::mem::size_of::<UinputSetup>()),
            &mut setup,
        )?;
        ioctl_int(fd, ioc(0, b'U', 1, 0), 0)?;
        Ok(Self {
            file,
            name: name.into(),
        })
    }
    pub fn clone_gamepad(device: &Device) -> io::Result<Self> {
        Self::create(
            &format!(
                "{VIRTUAL_PREFIX}-{}",
                device
                    .path
                    .file_name()
                    .unwrap_or_default()
                    .to_string_lossy()
            ),
            device.id,
            &device.keys,
            &device.abs,
            &[],
        )
    }
    pub fn pointer() -> io::Result<Self> {
        Self::create(
            &format!("{VIRTUAL_PREFIX}-pointer"),
            InputId {
                bustype: 3,
                ..InputId::default()
            },
            &[0x110, 0x111],
            &BTreeMap::new(),
            &[0, 1, 8],
        )
    }
    pub fn fixture() -> io::Result<Self> {
        let mut abs = BTreeMap::new();
        for c in [0, 1, 3, 4] {
            abs.insert(
                c,
                AbsInfo {
                    minimum: -32768,
                    maximum: 32767,
                    ..AbsInfo::default()
                },
            );
        }
        let keys = (0x130..=0x13e).collect::<Vec<_>>();
        Self::create(
            "mios-input-fixture-controller",
            InputId {
                bustype: 3,
                vendor: 0x1209,
                product: 1,
                version: 1,
            },
            &keys,
            &abs,
            &[],
        )
    }
    fn fixture_keyboard() -> io::Result<Self> {
        let mut abs = BTreeMap::new();
        for code in [0, 1] {
            abs.insert(
                code,
                AbsInfo {
                    minimum: -32768,
                    maximum: 32767,
                    ..AbsInfo::default()
                },
            );
        }
        Self::create(
            "mios-input-fixture-keyboard",
            InputId {
                bustype: 3,
                ..InputId::default()
            },
            &[0x130, 0x1c, 0x1e, 0x2c],
            &abs,
            &[],
        )
    }
    fn fixture_keyboard_joystick() -> io::Result<Self> {
        let mut abs = BTreeMap::new();
        for code in [0, 1] {
            abs.insert(
                code,
                AbsInfo {
                    minimum: -32768,
                    maximum: 32767,
                    ..AbsInfo::default()
                },
            );
        }
        Self::create(
            "mios-input-fixture-keyboard-joystick",
            InputId {
                bustype: 3,
                vendor: 0x04d9,
                product: 0xa293,
                version: 1,
            },
            &[0x130],
            &abs,
            &[],
        )
    }
    pub fn write(&mut self, events: &[Event]) -> io::Result<()> {
        for e in events {
            let event = InputEvent {
                time: libc::timeval {
                    tv_sec: 0,
                    tv_usec: 0,
                },
                kind: e.kind,
                code: e.code,
                value: e.value,
            };
            // SAFETY: all InputEvent bytes (including timeval) are initialized;
            // repr(C) has no padding on supported Linux input ABIs.
            let b = unsafe {
                std::slice::from_raw_parts(
                    (&event as *const InputEvent).cast::<u8>(),
                    std::mem::size_of::<InputEvent>(),
                )
            };
            self.file.write_all(b)?;
        }
        Ok(())
    }
    pub fn event_path(&self) -> io::Result<PathBuf> {
        let mut sys = [0u8; 128];
        ioctl_ptr(self.file.as_raw_fd(), ioc(2, b'U', 44, sys.len()), &mut sys)?;
        let end = sys.iter().position(|c| *c == 0).unwrap_or(sys.len());
        let sys = String::from_utf8_lossy(&sys[..end]);
        for entry in std::fs::read_dir(Path::new("/sys/devices/virtual/input").join(sys.as_ref()))?
        {
            let entry = entry?;
            if entry.file_name().to_string_lossy().starts_with("event") {
                let path=Path::new("/dev/input").join(entry.file_name());
                // devtmpfs publishes before udev sets the operator's input-group
                // access. The fixture must wait for usable permissions, not only
                // node existence (root-only tests otherwise hide this race).
                let _ready=OpenOptions::new().read(true).custom_flags(libc::O_NONBLOCK | libc::O_CLOEXEC | libc::O_NOFOLLOW).open(&path)?;
                return Ok(path);
            }
        }
        Err(io::Error::new(
            io::ErrorKind::NotFound,
            "virtual device event node not ready",
        ))
    }
}
impl Drop for VirtualDevice {
    fn drop(&mut self) {
        let _ = ioctl_int(self.file.as_raw_fd(), ioc(0, b'U', 2, 0), 0);
    }
}
pub fn singleton(path: &Path) -> io::Result<File> {
    let file = OpenOptions::new()
        .create(true)
        .truncate(false)
        .read(true)
        .write(true)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(path)?;
    // SAFETY: flock acts only on this valid descriptor; closing releases it.
    if unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } < 0 {
        return Err(io::Error::other(
            "input bridge already owns this session/device",
        ));
    }
    Ok(file)
}
pub fn discover() -> io::Result<Vec<PathBuf>> {
    let mut paths = std::fs::read_dir("/dev/input")?
        .filter_map(Result::ok)
        .map(|e| e.path())
        .filter(|p| {
            p.file_name()
                .is_some_and(|n| n.to_string_lossy().starts_with("event"))
        })
        .collect::<Vec<_>>();
    paths.sort();
    Ok(paths)
}
pub fn monotonic_ns() -> i64 {
    let mut t = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    // SAFETY: valid clock ID and initialized writable timespec.
    unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut t) };
    t.tv_sec
        .saturating_mul(1_000_000_000)
        .saturating_add(t.tv_nsec)
}
/// Creates only owned virtual hardware; never discovers/grabs a physical pad.
pub fn private_fixture() -> Result<serde_json::Value, String> {
    let keyboard = VirtualDevice::fixture_keyboard().map_err(|e| e.to_string())?;
    let mut keyboard_rejected = false;
    for _ in 0..100 {
        if let Ok(p) = keyboard.event_path() {
            if p.exists() {
                keyboard_rejected = Device::open(&p, true)
                    .err()
                    .is_some_and(|e| e.to_string().contains("typing keyboard"));
                break;
            }
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
    if !keyboard_rejected {
        return Err("composite keyboard was misclassified as a controller".into());
    }
    drop(keyboard);
    let keyboard_joystick =
        VirtualDevice::fixture_keyboard_joystick().map_err(|e| e.to_string())?;
    let mut quirk_rejected = false;
    for _ in 0..100 {
        if let Ok(p) = keyboard_joystick.event_path() {
            if p.exists() {
                quirk_rejected = Device::open(&p, true)
                    .err()
                    .is_some_and(|e| e.to_string().contains("keyboard HID quirk"));
                break;
            }
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
    if !quirk_rejected {
        return Err("keyboard auxiliary joystick interface was misclassified".into());
    }
    drop(keyboard_joystick);
    let mut producer = VirtualDevice::fixture().map_err(|e| e.to_string())?;
    let mut path = None;
    for _ in 0..100 {
        if let Ok(p) = producer.event_path() {
            if p.exists() {
                path = Some(p);
                break;
            }
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
    let path = path.ok_or("fixture did not enumerate")?;
    if !Device::open(&path,false).err().is_some_and(|e|e.to_string().contains("isolated from the live bridge")) {
        return Err("private fixture could enter live controller routing".into());
    }
    let mut input = Device::open(&path, true).map_err(|e| e.to_string())?;
    let mut other = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NONBLOCK)
        .open(&path)
        .map_err(|e| e.to_string())?;
    input.grab().map_err(|e| e.to_string())?;
    let sample = [
        Event::new(EV_KEY, 0x136, 1),
        Event::new(EV_SYN, SYN_REPORT, 0),
    ];
    producer.write(&sample).map_err(|e| e.to_string())?;
    std::thread::sleep(std::time::Duration::from_millis(20));
    let got = input.read_events().map_err(|e| e.to_string())?;
    if !got.iter().any(|e| *e == sample[0]) {
        return Err("fixture owner missed grabbed input".into());
    }
    let mut b = [0u8; 128];
    if !matches!(other.read(&mut b),Err(ref e)if e.kind()==io::ErrorKind::WouldBlock) {
        return Err("grabbed event leaked to second reader".into());
    }
    let mut output = VirtualDevice::clone_gamepad(&input).map_err(|e| e.to_string())?;
    let mut output_path = None;
    for _ in 0..100 {
        if let Ok(p) = output.event_path() {
            if p.exists() {
                output_path = Some(p);
                break;
            }
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
    let replay_rejected = output_path.is_some_and(|p| {
        Device::open(&p, false)
            .err()
            .is_some_and(|e| e.to_string().contains("replay loop"))
    });
    if !replay_rejected {
        return Err("virtual controller re-entered discovery".into());
    }
    // A focused app owns the virtual pad while the host retains physical input.
    let output_path = output.event_path().map_err(|e| e.to_string())?;
    let mut app = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NONBLOCK)
        .open(&output_path)
        .map_err(|e| e.to_string())?;
    ioctl_int(app.as_raw_fd(), ioc(1, b'E', 0x90, 4), 1).map_err(|e| e.to_string())?;
    let ordinary = [
        Event::new(EV_KEY, 0x131, 1),
        Event::new(EV_SYN, SYN_REPORT, 0),
    ];
    producer.write(&ordinary).map_err(|e| e.to_string())?;
    std::thread::sleep(std::time::Duration::from_millis(20));
    let incoming = input.read_events().map_err(|e| e.to_string())?;
    if !incoming.contains(&ordinary[0]) {
        return Err("app grabbing virtual output starved physical input".into());
    }
    output.write(&incoming).map_err(|e| e.to_string())?;
    std::thread::sleep(std::time::Duration::from_millis(20));
    if app.read(&mut b).is_err() {
        return Err("ordinary replay did not reach captured virtual app".into());
    }
    // A second bridge cannot own the same source, and its failed attempt must
    // not release the first owner's descriptor.
    let mut duplicate = Device::open(&path, true).map_err(|e| e.to_string())?;
    if duplicate.grab().is_ok() {
        return Err("duplicate physical owner accepted".into());
    }
    drop(duplicate);
    input.release();
    producer
        .write(&[
            Event::new(EV_KEY, 0x136, 0),
            Event::new(EV_SYN, SYN_REPORT, 0),
        ])
        .map_err(|e| e.to_string())?;
    std::thread::sleep(std::time::Duration::from_millis(20));
    if other.read(&mut b).is_err() {
        return Err("release did not restore other reader".into());
    }
    let mut signal_results = vec![];
    for signal in [libc::SIGTERM, libc::SIGKILL] {
        let mut child =
            std::process::Command::new(std::env::current_exe().map_err(|e| e.to_string())?)
                .arg("fixture-grab")
                .arg(&path)
                .stdout(std::process::Stdio::piped())
                .spawn()
                .map_err(|e| e.to_string())?;
        let mut stdout = child.stdout.take().ok_or("fixture child stdout missing")?;
        // SAFETY: fcntl changes only our valid pipe descriptor to nonblocking.
        unsafe {
            libc::fcntl(stdout.as_raw_fd(), libc::F_SETFL, libc::O_NONBLOCK);
        }
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(2);
        let mut ready = String::new();
        while std::time::Instant::now() < deadline {
            let mut chunk = [0u8; 64];
            match stdout.read(&mut chunk) {
                Ok(n) => ready.push_str(&String::from_utf8_lossy(&chunk[..n])),
                Err(e) if e.kind() == io::ErrorKind::WouldBlock => {}
                Err(e) => {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err(e.to_string());
                }
            }
            if ready.contains("ready") {
                break;
            }
            if child.try_wait().map_err(|e| e.to_string())?.is_some() {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(10));
        }
        if !ready.contains("ready") {
            let _ = child.kill();
            let _ = child.wait();
            return Err("fixture child failed to acquire its private device".into());
        }
        // SAFETY: this PID is our still-running owned fixture child.
        unsafe {
            libc::kill(child.id() as i32, signal);
        }
        child.wait().map_err(|e| e.to_string())?;
        // Drain previous events before proving a new press reaches the reader.
        while other.read(&mut b).is_ok() {}
        producer
            .write(&[
                Event::new(EV_KEY, 0x131, if signal == libc::SIGTERM { 0 } else { 1 }),
                Event::new(EV_SYN, SYN_REPORT, 0),
            ])
            .map_err(|e| e.to_string())?;
        std::thread::sleep(std::time::Duration::from_millis(20));
        if other.read(&mut b).is_err() {
            return Err(format!("signal {signal} left private device grabbed"));
        }
        signal_results.push(signal);
    }
    Ok(
        serde_json::json!({"fixture":"private-uinput","isolated_from_live_bridge":true,"grab_owner_received":true,"other_reader_blocked":true,"release_restored_reader":true,"replay_loop_rejected":true,"duplicate_owner_rejected":true,"typing_keyboard_rejected":true,"keyboard_aux_joystick_rejected":true,"virtual_app_grab_preserves_host_input":true,"ordinary_input_replayed":true,"release_after_signals":signal_results}),
    )
}

/// Only the parent fixture invokes this helper on its explicitly named virtual pad.
pub fn fixture_grab(path: &Path) -> Result<(), String> {
    let mut device = Device::open(path, true).map_err(|e| e.to_string())?;
    device.grab().map_err(|e| e.to_string())?;
    println!("ready");
    std::io::stdout().flush().map_err(|e| e.to_string())?;
    loop {
        std::thread::sleep(std::time::Duration::from_secs(1));
    }
}
