//! Capture of one Hyprland output with ext-image-copy-capture-v1 into a ring of GBM dmabufs, GPU
//! colour conversion (gpu.rs) and hand-over of the newest frame to the encoder (latest wins).
//!
//! Hyprland 0.56 blits each frame into our dmabuf on the GPU after it rendered the output, so
//! there is no CPU readback on its main loop (the shm path stalled it 13-22 ms per frame). It
//! completes a capture only when the output rendered, so an idle output costs nothing; it always
//! reports full damage, so region damage is computed by the encoder against the last encoded frame.
#![allow(unsafe_code)]

use std::collections::HashMap;
use std::fs::{File, OpenOptions};
use std::os::fd::{AsFd, AsRawFd, FromRawFd, OwnedFd};
use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

use anyhow::{Context as _, Result, anyhow, bail};
use gbm::{BufferObjectFlags, Format as GbmFormat, Modifier};
use tracing::{debug, info, warn};
use wayland_client::globals::{GlobalListContents, registry_queue_init};
use wayland_client::protocol::{wl_buffer, wl_output, wl_registry};
use wayland_client::backend::ObjectId;
use wayland_client::{Connection, Dispatch, EventQueue, Proxy, QueueHandle, WEnum, delegate_noop, event_created_child};
use wayland_protocols::ext::image_capture_source::v1::client::{
    ext_image_capture_source_v1::ExtImageCaptureSourceV1,
    ext_output_image_capture_source_manager_v1::ExtOutputImageCaptureSourceManagerV1,
};
use wayland_protocols::ext::image_copy_capture::v1::client::{
    ext_image_copy_capture_frame_v1::{self, ExtImageCopyCaptureFrameV1},
    ext_image_copy_capture_manager_v1::{self, ExtImageCopyCaptureManagerV1},
    ext_image_copy_capture_session_v1::{self, ExtImageCopyCaptureSessionV1},
};
use wayland_protocols::wp::linux_dmabuf::zv1::client::{
    zwp_linux_buffer_params_v1::{self, ZwpLinuxBufferParamsV1},
    zwp_linux_dmabuf_v1::ZwpLinuxDmabufV1,
};

use crate::gpu::{self, Gpu, Imported, Matrix, Plane};

/// What the capture thread produces.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PixelLayout {
    /// I420, row stride `stride`: Y rows, then H/2 rows of [U (stride/2) | V (stride/2)].
    I420,
    /// B,G,R,A bytes, stride W*4 (classic bitmap path).
    Bgra,
}

pub struct Frame {
    pub seq: u64,
    pub width: u32,
    pub height: u32,
    pub stride: u32,
    pub layout: PixelLayout,
    pub data: Vec<u8>,
    /// When Hyprland reported the frame ready.
    pub ready_at: Instant,
    /// capture() request to ready.
    pub capture_us: u64,
    /// GPU conversion + readback.
    pub convert_us: u64,
}

#[derive(Default)]
pub struct CaptureStats {
    pub frames: AtomicU64,
    pub failed: AtomicU64,
    pub convert_us_total: AtomicU64,
    pub capture_us_total: AtomicU64,
}

/// Newest-frame mailbox between the capture thread and the encoder.
#[derive(Default)]
pub struct Mailbox {
    slot: Mutex<MailboxInner>,
    cv: Condvar,
}

#[derive(Default)]
struct MailboxInner {
    frame: Option<Frame>,
    pool: Vec<Vec<u8>>,
    closed: Option<String>,
    size: Option<(u32, u32)>,
}

impl Mailbox {
    pub fn put(&self, f: Frame) {
        let mut s = self.slot.lock().expect("mailbox");
        if let Some(old) = s.frame.replace(f) {
            s.pool.push(old.data);
        }
        self.cv.notify_all();
    }
    /// Wait up to `timeout` for a frame; Err(reason) when capture ended.
    pub fn take(&self, timeout: Duration) -> Result<Option<Frame>, String> {
        let mut s = self.slot.lock().expect("mailbox");
        let deadline = Instant::now() + timeout;
        loop {
            if let Some(f) = s.frame.take() {
                return Ok(Some(f));
            }
            if let Some(r) = &s.closed {
                return Err(r.clone());
            }
            let now = Instant::now();
            if now >= deadline {
                return Ok(None);
            }
            s = self.cv.wait_timeout(s, deadline - now).expect("mailbox").0;
        }
    }
    pub fn recycle(&self, buf: Vec<u8>) {
        let mut s = self.slot.lock().expect("mailbox");
        if s.pool.len() < 4 {
            s.pool.push(buf);
        }
    }
    fn buffer(&self) -> Vec<u8> {
        self.slot.lock().expect("mailbox").pool.pop().unwrap_or_default()
    }
    fn close(&self, why: String) {
        let mut s = self.slot.lock().expect("mailbox");
        s.closed = Some(why);
        self.cv.notify_all();
    }
    pub fn notify(&self) {
        self.cv.notify_all();
    }
    fn set_size(&self, w: u32, h: u32) {
        self.slot.lock().expect("mailbox").size = Some((w, h));
    }
}

pub struct CaptureParams {
    pub output: String,
    pub paint_cursor: bool,
    pub max_hz: Arc<AtomicU32>,
    /// 0 = I420 (EGFX), 1 = BGRA (bitmap fallback)
    pub layout: Arc<AtomicU32>,
    pub paused: Arc<AtomicBool>,
    pub stop: Arc<AtomicBool>,
    pub matrix: Matrix,
    /// "auto" | "linear"
    pub modifiers: String,
    /// "auto" (udmabuf + CPU conversion, else GPU) | "cpu" | "gpu"
    pub path: String,
    pub convert_threads: usize,
    /// GPU path: "low" | "normal" EGL context priority
    pub gpu_priority: String,
    /// Wait this long for the output to appear.
    pub output_wait: Duration,
}

pub struct CaptureHandle {
    pub mailbox: Arc<Mailbox>,
    pub stats: Arc<CaptureStats>,
    pub info: Arc<Mutex<CaptureInfo>>,
    stop: Arc<AtomicBool>,
    join: Option<std::thread::JoinHandle<()>>,
}

#[derive(Default, Clone, Debug, serde::Serialize)]
pub struct CaptureInfo {
    pub renderer: String,
    pub low_priority_context: bool,
    pub format: String,
    pub modifier: String,
    pub planes: u32,
    pub size: (u32, u32),
    pub render_node: String,
    pub compositor_modifiers: Vec<String>,
    pub path: String,
}

impl CaptureHandle {
    pub fn stop(&mut self) {
        self.stop.store(true, Ordering::SeqCst);
        if let Some(j) = self.join.take() {
            let _ = j.join();
        }
    }
}

impl Drop for CaptureHandle {
    fn drop(&mut self) {
        self.stop();
    }
}

pub fn spawn(params: CaptureParams) -> Result<CaptureHandle> {
    let mailbox = Arc::new(Mailbox::default());
    let stats = Arc::new(CaptureStats::default());
    let info = Arc::new(Mutex::new(CaptureInfo::default()));
    let stop = Arc::clone(&params.stop);
    let (mb, st, inf) = (Arc::clone(&mailbox), Arc::clone(&stats), Arc::clone(&info));
    let join = std::thread::Builder::new()
        .name("mios-rdp-capture".into())
        .spawn(move || {
            let why = match run(&params, &mb, &st, &inf) {
                Ok(()) => "capture stopped".to_owned(),
                Err(e) => {
                    warn!("capture of {} ended: {e:#}", params.output);
                    format!("{e:#}")
                }
            };
            mb.close(why);
        })
        .context("spawn capture thread")?;
    Ok(CaptureHandle { mailbox, stats, info, stop, join: Some(join) })
}

enum Storage {
    /// GBM buffer object, converted by the GPU (EGL import + GLES shader).
    Gpu { _bo: gbm::BufferObject<()>, _fds: Vec<OwnedFd>, img: Option<Imported> },
    /// udmabuf over a sealed memfd: Hyprland's blit lands in CPU-cached memory, converted on the CPU
    /// (no GPU job of ours queues behind the compositor on a saturated iGPU).
    Cpu { map: memmap2::Mmap, dmabuf: OwnedFd, stride: u32, _memfd: File },
}

struct Buf {
    storage: Storage,
    wl: wl_buffer::WlBuffer,
    busy: bool,
}

#[derive(Default)]
struct Constraints {
    size: Option<(u32, u32)>,
    device: Option<u64>,
    formats: HashMap<u32, Vec<u64>>,
}

enum FrameState {
    Ready,
    Failed(u32),
}

enum ParamsState {
    Pending,
    Created(wl_buffer::WlBuffer),
    Failed,
}

struct State {
    outputs: Vec<(wl_output::WlOutput, Option<String>)>,
    pending: Constraints,
    current: Option<Constraints>,
    constraints_changed: bool,
    stopped: bool,
    frame: Option<(usize, FrameState)>,
    params: HashMap<ObjectId, ParamsState>,
}

impl Dispatch<wl_registry::WlRegistry, GlobalListContents> for State {
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

impl Dispatch<wl_output::WlOutput, ()> for State {
    fn event(state: &mut Self, o: &wl_output::WlOutput, e: wl_output::Event, _: &(), _: &Connection, _: &QueueHandle<Self>) {
        if let wl_output::Event::Name { name } = e {
            if let Some(slot) = state.outputs.iter_mut().find(|(p, _)| p == o) {
                slot.1 = Some(name);
            }
        }
    }
}

impl Dispatch<ExtImageCopyCaptureSessionV1, ()> for State {
    fn event(
        state: &mut Self,
        _: &ExtImageCopyCaptureSessionV1,
        e: ext_image_copy_capture_session_v1::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        use ext_image_copy_capture_session_v1::Event;
        match e {
            Event::BufferSize { width, height } => state.pending.size = Some((width, height)),
            Event::DmabufDevice { device } => {
                if device.len() >= 8 {
                    state.pending.device = Some(u64::from_ne_bytes(device[..8].try_into().expect("8 bytes")));
                }
            }
            Event::DmabufFormat { format, modifiers } => {
                let mods = modifiers.chunks_exact(8).map(|c| u64::from_ne_bytes(c.try_into().expect("8 bytes"))).collect();
                state.pending.formats.insert(format, mods);
            }
            Event::Done => {
                state.current = Some(std::mem::take(&mut state.pending));
                state.constraints_changed = true;
            }
            Event::Stopped => state.stopped = true,
            _ => {}
        }
    }
}

impl Dispatch<ExtImageCopyCaptureFrameV1, usize> for State {
    fn event(
        state: &mut Self,
        _: &ExtImageCopyCaptureFrameV1,
        e: ext_image_copy_capture_frame_v1::Event,
        idx: &usize,
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        use ext_image_copy_capture_frame_v1::Event;
        match e {
            Event::Ready => state.frame = Some((*idx, FrameState::Ready)),
            Event::Failed { reason } => {
                let r = match reason {
                    WEnum::Value(v) => v as u32,
                    WEnum::Unknown(u) => u,
                };
                state.frame = Some((*idx, FrameState::Failed(r)));
            }
            _ => {}
        }
    }
}

impl Dispatch<ZwpLinuxBufferParamsV1, ()> for State {
    fn event(
        state: &mut Self,
        p: &ZwpLinuxBufferParamsV1,
        e: zwp_linux_buffer_params_v1::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        match e {
            zwp_linux_buffer_params_v1::Event::Created { buffer } => {
                state.params.insert(p.id(), ParamsState::Created(buffer));
            }
            zwp_linux_buffer_params_v1::Event::Failed => {
                state.params.insert(p.id(), ParamsState::Failed);
            }
            _ => {}
        }
    }
    event_created_child!(State, ZwpLinuxBufferParamsV1, [
        zwp_linux_buffer_params_v1::EVT_CREATED_OPCODE => (wl_buffer::WlBuffer, ()),
    ]);
}

delegate_noop!(State: ignore ExtOutputImageCaptureSourceManagerV1);
delegate_noop!(State: ignore ExtImageCaptureSourceV1);
delegate_noop!(State: ignore ExtImageCopyCaptureManagerV1);
delegate_noop!(State: ignore ZwpLinuxDmabufV1);
delegate_noop!(State: ignore wl_buffer::WlBuffer);

/// Render node (renderD*) of the GPU whose dev_t Hyprland announced (its primary or render node).
pub fn render_node_for(dev: u64) -> Result<PathBuf> {
    let (maj, min) = (libc::major(dev), libc::minor(dev));
    let want = std::fs::canonicalize(format!("/sys/dev/char/{maj}:{min}/device")).ok();
    let mut nodes: Vec<PathBuf> = std::fs::read_dir("/dev/dri")
        .context("/dev/dri")?
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| p.file_name().and_then(|n| n.to_str()).is_some_and(|n| n.starts_with("renderD")))
        .collect();
    nodes.sort();
    for n in &nodes {
        let Ok(md) = std::fs::metadata(n) else { continue };
        if md.rdev() == dev {
            return Ok(n.clone());
        }
        let r = md.rdev();
        let dir = std::fs::canonicalize(format!("/sys/dev/char/{}:{}/device", libc::major(r), libc::minor(r))).ok();
        if want.is_some() && dir == want {
            return Ok(n.clone());
        }
    }
    nodes.into_iter().next().ok_or_else(|| anyhow!("no DRM render node"))
}

fn fourcc_name(f: u32) -> String {
    f.to_le_bytes().iter().map(|b| *b as char).collect()
}

fn pick_format(c: &Constraints) -> Option<(u32, Vec<u64>)> {
    for f in [gpu::DRM_FORMAT_XRGB8888, gpu::DRM_FORMAT_ARGB8888] {
        if let Some(m) = c.formats.get(&f) {
            return Some((f, m.clone()));
        }
    }
    None
}

fn wait_output(queue: &mut EventQueue<State>, state: &mut State, name: &str, wait: Duration) -> Result<wl_output::WlOutput> {
    let deadline = Instant::now() + wait;
    loop {
        queue.roundtrip(state).context("wayland roundtrip")?;
        if let Some((o, _)) = state.outputs.iter().find(|(_, n)| n.as_deref() == Some(name)) {
            return Ok(o.clone());
        }
        if Instant::now() >= deadline {
            let have: Vec<_> = state.outputs.iter().filter_map(|(_, n)| n.clone()).collect();
            bail!("output {name} not found (have {have:?})");
        }
        std::thread::sleep(Duration::from_millis(100));
    }
}

fn poll_read(queue: &mut EventQueue<State>, state: &mut State, timeout: Duration) -> Result<()> {
    match queue.flush() {
        Ok(()) => {}
        Err(wayland_client::backend::WaylandError::Io(e)) if e.kind() == std::io::ErrorKind::WouldBlock => {}
        Err(e) => return Err(e).context("wayland flush"),
    }
    if let Some(guard) = queue.prepare_read() {
        let fd = guard.connection_fd().as_raw_fd();
        let mut pfd = libc::pollfd { fd, events: libc::POLLIN, revents: 0 };
        let ms = timeout.as_millis().min(1000) as i32;
        // SAFETY: one valid pollfd.
        let r = unsafe { libc::poll(&mut pfd, 1, ms) };
        if r > 0 && (pfd.revents & libc::POLLIN) != 0 {
            match guard.read() {
                Ok(_) => {}
                Err(wayland_client::backend::WaylandError::Io(e)) if e.kind() == std::io::ErrorKind::WouldBlock => {}
                Err(e) => return Err(e).context("wayland read"),
            }
        } else {
            drop(guard);
        }
    }
    queue.dispatch_pending(state).context("wayland dispatch")?;
    Ok(())
}

const UDMABUF_CREATE: libc::c_ulong = 0x4018_7542;
const DMA_BUF_IOCTL_SYNC: libc::c_ulong = 0x4008_6200;
const DMA_BUF_SYNC_READ: u64 = 1;
const DMA_BUF_SYNC_END: u64 = 4;

#[repr(C)]
struct UdmabufCreate {
    memfd: u32,
    flags: u32,
    offset: u64,
    size: u64,
}

fn dmabuf_sync(fd: &OwnedFd, end: bool) {
    let flags: u64 = DMA_BUF_SYNC_READ | if end { DMA_BUF_SYNC_END } else { 0 };
    // SAFETY: struct dma_buf_sync { __u64 flags } on a dmabuf fd.
    unsafe {
        libc::ioctl(fd.as_raw_fd(), DMA_BUF_IOCTL_SYNC as _, &flags);
    }
}

/// One udmabuf of `size` bytes over a sealed memfd, mapped read-only.
fn udmabuf(size: usize) -> Result<(File, OwnedFd, memmap2::Mmap)> {
    let name = c"mios-rdp-capture";
    // SAFETY: memfd_create/ftruncate/fcntl/ioctl on descriptors owned here.
    unsafe {
        let fd = libc::memfd_create(name.as_ptr(), libc::MFD_CLOEXEC | libc::MFD_ALLOW_SEALING);
        if fd < 0 {
            bail!("memfd_create: {}", std::io::Error::last_os_error());
        }
        let memfd = File::from_raw_fd(fd);
        if libc::ftruncate(fd, size as libc::off_t) != 0 {
            bail!("ftruncate: {}", std::io::Error::last_os_error());
        }
        if libc::fcntl(fd, libc::F_ADD_SEALS, libc::F_SEAL_SHRINK) != 0 {
            bail!("F_SEAL_SHRINK: {}", std::io::Error::last_os_error());
        }
        let dev = OpenOptions::new().read(true).write(true).custom_flags(libc::O_CLOEXEC).open("/dev/udmabuf").context("open /dev/udmabuf")?;
        let req = UdmabufCreate { memfd: fd as u32, flags: 0x01 /* UDMABUF_FLAGS_CLOEXEC */, offset: 0, size: size as u64 };
        let dfd = libc::ioctl(dev.as_raw_fd(), UDMABUF_CREATE as _, &req);
        if dfd < 0 {
            bail!("UDMABUF_CREATE: {}", std::io::Error::last_os_error());
        }
        let dmabuf = OwnedFd::from_raw_fd(dfd);
        let map = memmap2::MmapOptions::new().len(size).map(&memfd).context("mmap capture memfd")?;
        Ok((memfd, dmabuf, map))
    }
}

/// Create a wl_buffer through zwp_linux_buffer_params_v1.create and wait for created/failed (a
/// refused import must not cost the connection, as create_immed may).
fn create_buffer(
    queue: &mut EventQueue<State>,
    state: &mut State,
    dmabuf: &ZwpLinuxDmabufV1,
    qh: &QueueHandle<State>,
    planes: &[(&OwnedFd, u32, u32)],
    w: u32,
    h: u32,
    fmt: u32,
    modifier: u64,
) -> Result<wl_buffer::WlBuffer> {
    let params = dmabuf.create_params(qh, ());
    for (i, (fd, off, stride)) in planes.iter().enumerate() {
        params.add(fd.as_fd(), i as u32, *off, *stride, (modifier >> 32) as u32, modifier as u32);
    }
    let id = params.id();
    state.params.insert(id.clone(), ParamsState::Pending);
    params.create(w as i32, h as i32, fmt, zwp_linux_buffer_params_v1::Flags::empty());
    let deadline = Instant::now() + Duration::from_secs(3);
    let r = loop {
        queue.roundtrip(state).context("roundtrip")?;
        match state.params.remove(&id) {
            Some(ParamsState::Created(b)) => break Ok(b),
            Some(ParamsState::Failed) => break Err(anyhow!("compositor refused the dmabuf import")),
            Some(ParamsState::Pending) | None => {
                state.params.insert(id.clone(), ParamsState::Pending);
                if Instant::now() > deadline {
                    break Err(anyhow!("no created/failed for the dmabuf within 3 s"));
                }
            }
        }
    };
    state.params.remove(&id);
    params.destroy();
    r
}

fn release_all(bufs: &mut Vec<Buf>, gpu: &Option<Gpu>) {
    for b in bufs.drain(..) {
        if let Storage::Gpu { img: Some(img), .. } = b.storage {
            if let Some(g) = gpu.as_ref() {
                g.release(img);
            }
        }
        b.wl.destroy();
    }
}

#[allow(clippy::too_many_lines)]
fn run(p: &CaptureParams, mailbox: &Mailbox, stats: &CaptureStats, info: &Mutex<CaptureInfo>) -> Result<()> {
    let conn = Connection::connect_to_env().context("connect to the Wayland session")?;
    let (globals, mut queue) = registry_queue_init::<State>(&conn).context("wayland registry")?;
    let qh = queue.handle();
    let mut state = State {
        outputs: Vec::new(),
        pending: Constraints::default(),
        current: None,
        constraints_changed: false,
        stopped: false,
        frame: None,
        params: HashMap::new(),
    };
    for g in globals.contents().clone_list() {
        if g.interface == wl_output::WlOutput::interface().name {
            let o = globals.registry().bind::<wl_output::WlOutput, _, _>(g.name, g.version.min(4), &qh, ());
            state.outputs.push((o, None));
        }
    }
    let src_mgr: ExtOutputImageCaptureSourceManagerV1 =
        globals.bind(&qh, 1..=1, ()).context("ext_output_image_capture_source_manager_v1")?;
    let cap_mgr: ExtImageCopyCaptureManagerV1 = globals.bind(&qh, 1..=1, ()).context("ext_image_copy_capture_manager_v1")?;
    let dmabuf: ZwpLinuxDmabufV1 = globals.bind(&qh, 3..=4, ()).context("zwp_linux_dmabuf_v1 >= 3")?;

    let output = wait_output(&mut queue, &mut state, &p.output, p.output_wait)?;
    let source = src_mgr.create_source(&output, &qh, ());
    let options = if p.paint_cursor {
        ext_image_copy_capture_manager_v1::Options::PaintCursors
    } else {
        ext_image_copy_capture_manager_v1::Options::empty()
    };
    let session = cap_mgr.create_session(&source, options, &qh, ());
    info!(output = %p.output, paint_cursor = p.paint_cursor, path = %p.path, "capture session created");

    let mut gbm_dev: Option<gbm::Device<File>> = None;
    let mut gpu_ctx: Option<Gpu> = None;
    let mut bufs: Vec<Buf> = Vec::new();
    let mut geom = (0u32, 0u32, 0u32, 0u64); // w, h, format, modifier
    let mut seq = 0u64;
    let mut next_allowed = Instant::now();
    let mut requested_at = Instant::now();
    let mut active: Option<ExtImageCopyCaptureFrameV1> = None;
    let mut out_of_sync = false;
    let mut use_cpu = p.path != "gpu";

    loop {
        if p.stop.load(Ordering::SeqCst) {
            break;
        }
        if state.stopped {
            release_all(&mut bufs, &gpu_ctx);
            bail!("compositor stopped the capture session (output {} gone?)", p.output);
        }
        if state.constraints_changed && active.is_none() {
            state.constraints_changed = false;
            let c = state.current.take().ok_or_else(|| anyhow!("no constraints"))?;
            let (w, h) = c.size.ok_or_else(|| anyhow!("no buffer_size"))?;
            let (fmt, comp_mods) = pick_format(&c).ok_or_else(|| anyhow!("no XRGB/ARGB8888 dmabuf format offered"))?;
            info.lock().expect("info").compositor_modifiers = comp_mods.iter().map(|m| format!("{m:#x}")).collect();
            let dev = c.device;
            state.current = Some(c);
            if (w, h, fmt) != (geom.0, geom.1, geom.2) || bufs.is_empty() {
                release_all(&mut bufs, &gpu_ctx);
                // CPU path: LINEAR udmabufs (needs the compositor to offer LINEAR and /dev/udmabuf)
                if use_cpu && !comp_mods.contains(&gpu::DRM_FORMAT_MOD_LINEAR) {
                    info!("compositor offers no LINEAR capture buffers; GPU conversion");
                    use_cpu = false;
                }
                if use_cpu {
                    let stride = (w * 4).div_ceil(256) * 256;
                    let size = ((stride * h) as usize).div_ceil(4096) * 4096;
                    let mut ok = true;
                    for _ in 0..3 {
                        match udmabuf(size).and_then(|(memfd, dfd, map)| {
                            let wl = create_buffer(&mut queue, &mut state, &dmabuf, &qh, &[(&dfd, 0, stride)], w, h, fmt, gpu::DRM_FORMAT_MOD_LINEAR)?;
                            Ok(Buf { storage: Storage::Cpu { map, dmabuf: dfd, stride, _memfd: memfd }, wl, busy: false })
                        }) {
                            Ok(b) => bufs.push(b),
                            Err(e) => {
                                warn!("udmabuf capture buffer refused ({e:#}); GPU conversion");
                                ok = false;
                                break;
                            }
                        }
                    }
                    if ok {
                        geom = (w, h, fmt, gpu::DRM_FORMAT_MOD_LINEAR);
                        let mut i = info.lock().expect("info");
                        i.format = fourcc_name(fmt);
                        i.modifier = "0x0 (LINEAR udmabuf)".into();
                        i.planes = 1;
                        i.size = (w, h);
                        i.path = format!("cpu ({} threads)", p.convert_threads);
                    } else {
                        release_all(&mut bufs, &gpu_ctx);
                        use_cpu = false;
                    }
                }
                if !use_cpu {
                    if gbm_dev.is_none() {
                        let dev = dev.ok_or_else(|| anyhow!("no dmabuf_device"))?;
                        let node = render_node_for(dev)?;
                        let f = OpenOptions::new()
                            .read(true)
                            .write(true)
                            .custom_flags(libc::O_CLOEXEC)
                            .open(&node)
                            .with_context(|| format!("open {}", node.display()))?;
                        let g = gbm::Device::new(f).context("gbm_create_device")?;
                        let raw = gbm::AsRaw::as_raw(&g) as *mut std::ffi::c_void;
                        let ctx = Gpu::new(raw, p.matrix, p.gpu_priority != "normal")?;
                        info!(node = %node.display(), renderer = %ctx.renderer, low_priority = ctx.low_priority, "GPU conversion ready");
                        let mut i = info.lock().expect("info");
                        i.renderer = ctx.renderer.clone();
                        i.low_priority_context = ctx.low_priority;
                        i.render_node = node.display().to_string();
                        drop(i);
                        gbm_dev = Some(g);
                        gpu_ctx = Some(ctx);
                    }
                    let g = gbm_dev.as_ref().expect("gbm");
                    let ctx = gpu_ctx.as_ref().expect("gpu");
                    let egl_mods = ctx.importable_modifiers(fmt);
                    let mut mods: Vec<u64> = comp_mods
                        .iter()
                        .copied()
                        .filter(|m| *m != gpu::DRM_FORMAT_MOD_INVALID && egl_mods.iter().any(|(e, _)| e == m))
                        .collect();
                    if p.modifiers == "linear" {
                        mods.retain(|m| *m == gpu::DRM_FORMAT_MOD_LINEAR);
                    }
                    let gfmt = GbmFormat::try_from(fmt).map_err(|_| anyhow!("gbm format"))?;
                    for _ in 0..3 {
                        let bo = if mods.is_empty() {
                            g.create_buffer_object::<()>(w, h, gfmt, BufferObjectFlags::RENDERING)
                        } else {
                            g.create_buffer_object_with_modifiers2::<()>(w, h, gfmt, mods.iter().map(|m| Modifier::from(*m)), BufferObjectFlags::RENDERING)
                        }
                        .context("gbm_bo_create")?;
                        let modifier: u64 = if mods.is_empty() { gpu::DRM_FORMAT_MOD_INVALID } else { bo.modifier().into() };
                        let n = bo.plane_count() as i32;
                        let mut fds = Vec::new();
                        for i in 0..n {
                            fds.push(bo.fd_for_plane(i).map_err(|e| anyhow!("gbm plane fd: {e:?}"))?);
                        }
                        let pl: Vec<(&OwnedFd, u32, u32)> =
                            (0..n).map(|i| (&fds[i as usize], bo.offset(i), bo.stride_for_plane(i))).collect();
                        let wl = create_buffer(&mut queue, &mut state, &dmabuf, &qh, &pl, w, h, fmt, modifier)?;
                        let planes: Vec<Plane<'_>> = (0..n)
                            .map(|i| Plane { fd: &fds[i as usize], offset: bo.offset(i), stride: bo.stride_for_plane(i) })
                            .collect();
                        let img = ctx.import(w, h, fmt, modifier, &planes)?;
                        drop(planes);
                        drop(pl);
                        geom = (w, h, fmt, modifier);
                        let mut i = info.lock().expect("info");
                        i.format = fourcc_name(fmt);
                        i.modifier = format!("{modifier:#018x}");
                        i.planes = n as u32;
                        i.size = (w, h);
                        i.path = "gpu".into();
                        drop(i);
                        bufs.push(Buf { storage: Storage::Gpu { _bo: bo, _fds: fds, img: Some(img) }, wl, busy: false });
                    }
                }
                mailbox.set_size(w, h);
                info!(w, h, format = %fourcc_name(fmt), modifier = format!("{:#x}", geom.3), path = if use_cpu { "cpu" } else { "gpu" }, "capture buffers allocated");
            }
            out_of_sync = false;
        }

        let now = Instant::now();
        let paused = p.paused.load(Ordering::Relaxed);
        if active.is_none() && !paused && !bufs.is_empty() && !out_of_sync && now >= next_allowed {
            if let Some(f) = request(&session, &qh, &mut bufs, geom) {
                requested_at = now;
                active = Some(f);
            }
        }
        let wait = if active.is_some() {
            Duration::from_millis(100)
        } else if paused || bufs.is_empty() || out_of_sync {
            Duration::from_millis(50)
        } else {
            next_allowed.saturating_duration_since(Instant::now()).max(Duration::from_millis(1))
        };
        poll_read(&mut queue, &mut state, wait)?;

        let Some((idx, st)) = state.frame.take() else {
            if active.is_some() && requested_at.elapsed() > Duration::from_secs(5) && state.constraints_changed {
                // constraints re-announced while a frame waited: drop it and re-allocate
                if let Some(f) = active.take() {
                    f.destroy();
                }
                for b in bufs.iter_mut() {
                    b.busy = false;
                }
            }
            continue;
        };
        if let Some(f) = active.take() {
            f.destroy();
        }
        match st {
            FrameState::Ready => {
                let ready_at = Instant::now();
                let capture_us = ready_at.duration_since(requested_at).as_micros() as u64;
                let hz = p.max_hz.load(Ordering::Relaxed).max(1);
                next_allowed = requested_at + Duration::from_micros(1_000_000 / u64::from(hz));
                // pipeline: Hyprland fills the next buffer while this one is converted
                bufs[idx].busy = true;
                if !p.paused.load(Ordering::Relaxed) && !state.constraints_changed && Instant::now() >= next_allowed {
                    if let Some(f) = request(&session, &qh, &mut bufs, geom) {
                        requested_at = Instant::now();
                        active = Some(f);
                        let _ = queue.flush();
                    }
                }
                let layout = if p.layout.load(Ordering::Relaxed) == 1 { PixelLayout::Bgra } else { PixelLayout::I420 };
                let (w, h) = (geom.0, geom.1);
                let mut data = mailbox.buffer();
                let t0 = Instant::now();
                let skip_convert = std::env::var_os("MIOS_RDP_BENCH_NOCONVERT").is_some();
                let wp = w.div_ceil(16) * 16;
                let (stride, res): (u32, Result<()>) = match (&bufs[idx].storage, layout) {
                    _ if skip_convert => (w, Ok(())),
                    (Storage::Cpu { map, dmabuf, stride, .. }, PixelLayout::I420) => {
                        dmabuf_sync(dmabuf, false);
                        crate::convert::xrgb_to_i420(map, *stride as usize, w as usize & !1, h as usize & !1, wp as usize, p.matrix, p.convert_threads, &mut data);
                        dmabuf_sync(dmabuf, true);
                        (wp, Ok(()))
                    }
                    (Storage::Cpu { map, dmabuf, stride, .. }, PixelLayout::Bgra) => {
                        dmabuf_sync(dmabuf, false);
                        data.clear();
                        data.extend_from_slice(&map[..(*stride * h) as usize]);
                        dmabuf_sync(dmabuf, true);
                        (*stride, Ok(()))
                    }
                    (Storage::Gpu { img: Some(img), .. }, PixelLayout::I420) => {
                        let ctx = gpu_ctx.as_mut().expect("gpu");
                        (wp, ctx.convert_i420(img, w, h & !1, wp, &mut data).map(|_| ()))
                    }
                    (Storage::Gpu { img: Some(img), .. }, PixelLayout::Bgra) => {
                        let ctx = gpu_ctx.as_mut().expect("gpu");
                        (w * 4, ctx.convert_bgra(img, w, h, &mut data).map(|_| ()))
                    }
                    (Storage::Gpu { img: None, .. }, _) => (w, Err(anyhow!("no EGL image"))),
                };
                bufs[idx].busy = false;
                match res {
                    Ok(()) => {
                        let convert_us = t0.elapsed().as_micros() as u64;
                        seq += 1;
                        stats.frames.fetch_add(1, Ordering::Relaxed);
                        stats.convert_us_total.fetch_add(convert_us, Ordering::Relaxed);
                        stats.capture_us_total.fetch_add(capture_us, Ordering::Relaxed);
                        mailbox.put(Frame {
                            seq,
                            width: w,
                            height: if layout == PixelLayout::I420 { h & !1 } else { h },
                            stride,
                            layout,
                            data,
                            ready_at,
                            capture_us,
                            convert_us,
                        });
                    }
                    Err(e) => {
                        stats.failed.fetch_add(1, Ordering::Relaxed);
                        warn!("conversion failed: {e:#}");
                        mailbox.recycle(data);
                    }
                }
            }
            FrameState::Failed(reason) => {
                bufs[idx].busy = false;
                stats.failed.fetch_add(1, Ordering::Relaxed);
                // 1 = buffer_constraints: new constraints (resize) follow with done; 2 = stopped
                debug!(reason, "capture frame failed");
                if reason == 1 {
                    out_of_sync = true;
                } else if reason == 2 {
                    state.stopped = true;
                }
                next_allowed = Instant::now() + Duration::from_millis(5);
            }
        }
    }
    if let Some(f) = active.take() {
        f.destroy();
    }
    release_all(&mut bufs, &gpu_ctx);
    session.destroy();
    source.destroy();
    let _ = queue.roundtrip(&mut state);
    drop(gpu_ctx);
    Ok(())
}

fn request(
    session: &ExtImageCopyCaptureSessionV1,
    qh: &QueueHandle<State>,
    bufs: &mut [Buf],
    geom: (u32, u32, u32, u64),
) -> Option<ExtImageCopyCaptureFrameV1> {
    let idx = bufs.iter().position(|b| !b.busy)?;
    let f = session.create_frame(qh, idx);
    f.attach_buffer(&bufs[idx].wl);
    f.damage_buffer(0, 0, geom.0 as i32, geom.1 as i32);
    f.capture();
    bufs[idx].busy = true;
    Some(f)
}

/// Path helper for tests/self-test.
pub fn default_render_node() -> Option<PathBuf> {
    let p = Path::new("/dev/dri/renderD128");
    p.exists().then(|| p.to_path_buf())
}
