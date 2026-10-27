//! EGFX (MS-RDPEGFX) factory/handler and the encode pipeline thread: newest captured I420 frame ->
//! damage against the last ENCODED frame (16x16 macroblock grid, <= 16 region rects) -> OpenH264
//! -> AVC420 WireToSurface1 with region rects -> ServerEvent::Egfx. Frame acks bound the frames in
//! flight (backpressure) and drive the AIMD bitrate controller; delivered frames/s = acks/s.

use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

use ironrdp_egfx::pdu::{Avc420Region, CapabilitiesAdvertisePdu, CapabilitySet};
use ironrdp_egfx::server::{GraphicsPipelineHandler, GraphicsPipelineServer, QoeMetrics};
use ironrdp_server::{GfxDvcBridge, GfxServerFactory, GfxServerHandle, ServerEvent, ServerEventSender};
use tokio::sync::mpsc::UnboundedSender;
use tracing::{debug, info, warn};

use crate::capture::{Frame, Mailbox, PixelLayout};
use crate::config::Native;
use crate::encode::{EncoderSettings, H264Encoder, I420, OpenH264};

/// State shared by the IronRDP callbacks, the pipeline thread and the stats loop. Reset per connection.
#[derive(Default)]
pub struct Gfx {
    pub handle: Mutex<Option<GfxServerHandle>>,
    pub sender: Mutex<Option<UnboundedSender<ServerEvent>>>,
    pub ready: AtomicBool,
    pub closed: AtomicBool,
    pub avc420: AtomicBool,
    pub avc444: AtomicBool,
    pub advertised: Mutex<Vec<String>>,
    pub negotiated: Mutex<String>,
    pub acks: Mutex<Acks>,
    pub wake: Condvar,
    pub wake_lock: Mutex<()>,
    pub generation: AtomicU64,
}

#[derive(Default)]
pub struct Acks {
    pub sent: HashMap<u32, (Instant, Instant, usize)>, // frame id -> (capture ready, sent, bytes)
    pub acked: u64,
    pub ack_ms: Vec<f64>,
    pub glass_ms: Vec<f64>, // capture ready -> ack (end-to-end incl. network + client decode)
    pub last_queue_depth: u32,
    pub decoded_total: u32,
    pub qoe_dr_us: Vec<u32>,
}

impl Gfx {
    pub fn reset(&self) {
        *self.handle.lock().expect("gfx") = None;
        self.ready.store(false, Ordering::SeqCst);
        self.closed.store(false, Ordering::SeqCst);
        self.avc420.store(false, Ordering::SeqCst);
        self.avc444.store(false, Ordering::SeqCst);
        self.advertised.lock().expect("gfx").clear();
        self.negotiated.lock().expect("gfx").clear();
        *self.acks.lock().expect("gfx") = Acks::default();
        self.generation.fetch_add(1, Ordering::SeqCst);
    }
    pub fn notify(&self) {
        let _g = self.wake_lock.lock().expect("gfx");
        self.wake.notify_all();
    }
    fn wait(&self, d: Duration) {
        let g = self.wake_lock.lock().expect("gfx");
        let _ = self.wake.wait_timeout(g, d);
    }
}

pub struct GfxFactory {
    pub gfx: Arc<Gfx>,
    pub max_in_flight: u32,
}

impl ServerEventSender for GfxFactory {
    fn set_sender(&mut self, sender: UnboundedSender<ServerEvent>) {
        *self.gfx.sender.lock().expect("gfx") = Some(sender);
    }
}

impl GfxServerFactory for GfxFactory {
    fn build_gfx_handler(&self) -> Box<dyn GraphicsPipelineHandler> {
        Box::new(Handler { gfx: Arc::clone(&self.gfx), max_in_flight: self.max_in_flight })
    }
    fn build_server_with_handle(&self) -> Option<(GfxDvcBridge, GfxServerHandle)> {
        // Built per connection before authentication: nothing here may have side effects.
        let server = GraphicsPipelineServer::new(self.build_gfx_handler());
        let handle: GfxServerHandle = Arc::new(Mutex::new(server));
        *self.gfx.handle.lock().expect("gfx") = Some(Arc::clone(&handle));
        Some((GfxDvcBridge::new(Arc::clone(&handle)), handle))
    }
}

struct Handler {
    gfx: Arc<Gfx>,
    max_in_flight: u32,
}

impl GraphicsPipelineHandler for Handler {
    fn capabilities_advertise(&mut self, pdu: &CapabilitiesAdvertisePdu) {
        let caps: Vec<String> = pdu.0.iter().map(|c| format!("{c:?}")).collect();
        info!(caps = ?caps, "EGFX client capabilities advertised");
        *self.gfx.advertised.lock().expect("gfx") = caps;
    }
    fn on_ready(&mut self, negotiated: &CapabilitySet) {
        // Called from inside GraphicsPipelineServer::process (its mutex is held): codec support is
        // read by the pipeline thread once `ready` flips.
        info!(negotiated = ?negotiated, "EGFX ready");
        *self.gfx.negotiated.lock().expect("gfx") = format!("{negotiated:?}");
        self.gfx.ready.store(true, Ordering::SeqCst);
        self.gfx.notify();
    }
    fn on_frame_ack(&mut self, frame_id: u32, queue_depth: u32, total_frames_decoded: u32) {
        let now = Instant::now();
        let mut a = self.gfx.acks.lock().expect("gfx");
        if let Some((ready, sent, _)) = a.sent.remove(&frame_id) {
            a.ack_ms.push(now.duration_since(sent).as_secs_f64() * 1000.0);
            a.glass_ms.push(now.duration_since(ready).as_secs_f64() * 1000.0);
        }
        a.acked += 1;
        a.last_queue_depth = queue_depth;
        a.decoded_total = total_frames_decoded;
        drop(a);
        self.gfx.notify();
    }
    fn on_qoe_metrics(&mut self, m: QoeMetrics) {
        self.gfx.acks.lock().expect("gfx").qoe_dr_us.push(u32::from(m.time_diff_dr));
    }
    fn on_close(&mut self) {
        info!("EGFX channel closed");
        self.gfx.ready.store(false, Ordering::SeqCst);
        self.gfx.closed.store(true, Ordering::SeqCst);
        self.gfx.notify();
    }
    fn max_frames_in_flight(&self) -> u32 {
        self.max_in_flight.max(1)
    }
}

/// Counters the stats loop turns into rates every window.
#[derive(Default)]
pub struct PipeStats {
    pub captured: AtomicU64,
    pub encoded: AtomicU64,
    pub idr: AtomicU64,
    pub skipped_static: AtomicU64,
    pub skipped_rc: AtomicU64,
    pub backpressured: AtomicU64,
    pub refined: AtomicU64,
    pub bytes: AtomicU64,
    pub bitrate_bps: AtomicU64,
    pub frames_in_flight: AtomicU32,
    pub samples: Mutex<Samples>,
    pub last_level: Mutex<Option<(u8, u8)>>,
    pub last_slices: AtomicU32,
    pub bitmap_frames: AtomicU64,
    pub bitmap_mode: AtomicBool,
    pub surface: Mutex<(u16, u16)>,
}

#[derive(Default)]
pub struct Samples {
    pub encode_us: Vec<u64>,
    pub convert_us: Vec<u64>,
    pub capture_to_send_us: Vec<u64>,
    pub damage_pct: Vec<f64>,
}

pub struct PipeParams {
    pub native: Native,
    pub target_hz: u32,
    pub bitmap_tx: tokio::sync::mpsc::Sender<Frame>,
    /// 0 = I420/EGFX, 1 = BGRA/bitmap (shared with the capture thread)
    pub layout: Arc<AtomicU32>,
    pub capture_hz: Arc<AtomicU32>,
    pub authenticated: Arc<AtomicBool>,
    pub suppressed: Arc<std::sync::atomic::AtomicBool>,
    pub stop: Arc<AtomicBool>,
    pub refresh_request: Arc<AtomicBool>,
    pub egfx_wait: Duration,
}

/// Damage of `cur` against `prev` on the 16x16 macroblock grid of the luma plane (chroma blocks
/// are 8x8 and map 1:1). Returns region rects (exclusive right/bottom, clipped to w x h) and the
/// damaged share in percent. None = no change.
pub fn damage(prev: &[u8], cur: &[u8], w: u32, h: u32, stride: u32, max_rects: usize) -> Option<(Vec<Avc420Region>, f64)> {
    let mbw = w.div_ceil(16) as usize;
    let mbh = h.div_ceil(16) as usize;
    let s = stride as usize;
    let (w, h) = (w as usize, h as usize);
    let mut dirty = vec![false; mbw * mbh];
    // luma
    for y in 0..h {
        let row = y * s;
        let mb_row = y / 16;
        for bx in 0..mbw {
            if dirty[mb_row * mbw + bx] {
                continue;
            }
            let x0 = bx * 16;
            let x1 = (x0 + 16).min(w);
            if prev[row + x0..row + x1] != cur[row + x0..row + x1] {
                dirty[mb_row * mbw + bx] = true;
            }
        }
    }
    // chroma ([U | V] per row, each stride/2 wide)
    let base = s * h;
    let half = s / 2;
    let cw = w.div_ceil(2);
    for cy in 0..h / 2 {
        let row = base + cy * s;
        let mb_row = cy / 8;
        for bx in 0..mbw {
            if dirty[mb_row * mbw + bx] {
                continue;
            }
            let x0 = bx * 8;
            let x1 = (x0 + 8).min(cw);
            if x0 >= x1 {
                continue;
            }
            if prev[row + x0..row + x1] != cur[row + x0..row + x1]
                || prev[row + half + x0..row + half + x1] != cur[row + half + x0..row + half + x1]
            {
                dirty[mb_row * mbw + bx] = true;
            }
        }
    }
    let ndirty = dirty.iter().filter(|d| **d).count();
    if ndirty == 0 {
        return None;
    }
    // one band per run of MB rows, x-extent = dirty columns; then merge closest bands to <= max_rects
    let mut bands: Vec<(usize, usize, usize, usize)> = Vec::new(); // (row0, row1 excl, col0, col1 excl)
    for r in 0..mbh {
        let cols: Vec<usize> = (0..mbw).filter(|c| dirty[r * mbw + c]).collect();
        let (Some(c0), Some(c1)) = (cols.first().copied(), cols.last().copied()) else { continue };
        match bands.last_mut() {
            Some(b) if b.1 == r && b.2 == c0 && b.3 == c1 + 1 => b.1 = r + 1,
            _ => bands.push((r, r + 1, c0, c1 + 1)),
        }
    }
    while bands.len() > max_rects.max(1) {
        // merge the adjacent pair whose union adds the least area
        let mut best = (usize::MAX, 0usize);
        for i in 0..bands.len() - 1 {
            let (a, b) = (bands[i], bands[i + 1]);
            let u = (a.0.min(b.0), a.1.max(b.1), a.2.min(b.2), a.3.max(b.3));
            let cost = (u.1 - u.0) * (u.3 - u.2) - (a.1 - a.0) * (a.3 - a.2) - (b.1 - b.0) * (b.3 - b.2);
            if cost < best.0 {
                best = (cost, i);
            }
        }
        let i = best.1;
        let (a, b) = (bands[i], bands.remove(i + 1));
        bands[i] = (a.0.min(b.0), a.1.max(b.1), a.2.min(b.2), a.3.max(b.3));
    }
    let rects = bands
        .into_iter()
        .map(|(r0, r1, c0, c1)| Avc420Region {
            left: (c0 * 16) as u16,
            top: (r0 * 16) as u16,
            right: ((c1 * 16).min(w)) as u16,
            bottom: ((r1 * 16).min(h)) as u16,
            quantization_parameter: 22,
            quality: 100,
        })
        .collect();
    Some((rects, 100.0 * ndirty as f64 / (mbw * mbh) as f64))
}

fn full_rect(w: u32, h: u32) -> Vec<Avc420Region> {
    vec![Avc420Region { left: 0, top: 0, right: w as u16, bottom: h as u16, quantization_parameter: 22, quality: 100 }]
}

/// AIMD bitrate controller (rdp-native-design.md 4.3).
pub struct Aimd {
    pub bps: u32,
    min: u32,
    max: u32,
    last: Instant,
}

impl Aimd {
    pub fn new(initial: u32, min: u32, max: u32) -> Self {
        Self { bps: initial.clamp(min, max), min, max, last: Instant::now() }
    }
    /// One decision per second: `congested` = the window was backpressured a lot or acks were slow.
    pub fn tick(&mut self, congested: bool, now: Instant) -> Option<u32> {
        if now.duration_since(self.last) < Duration::from_secs(1) {
            return None;
        }
        self.last = now;
        let old = self.bps;
        self.bps = if congested {
            ((f64::from(self.bps) * 0.85) as u32).max(self.min)
        } else {
            ((f64::from(self.bps) * 1.05) as u32).min(self.max)
        };
        (self.bps != old).then_some(self.bps)
    }
}

pub fn spawn(
    p: PipeParams,
    gfx: Arc<Gfx>,
    mailbox: Arc<Mailbox>,
    stats: Arc<PipeStats>,
) -> std::thread::JoinHandle<()> {
    std::thread::Builder::new()
        .name("mios-rdp-encode".into())
        .spawn(move || {
            if let Err(e) = run(&p, &gfx, &mailbox, &stats) {
                warn!("encode pipeline ended: {e:#}");
            }
        })
        .expect("spawn encode thread")
}

struct Surface {
    id: u16,
    w: u32,
    h: u32,
    generation: u64,
}

fn submit(gfx: &Gfx, surface: u16, data: &[u8], regions: &[Avc420Region], ts_ms: u32) -> Result<Option<u32>, String> {
    let handle = gfx.handle.lock().expect("gfx").clone().ok_or("no EGFX server")?;
    let (frame_id, msgs, channel) = {
        let mut s = handle.lock().map_err(|_| "EGFX lock poisoned")?;
        let frame_id = match s.try_send_avc420_frame(surface, data, regions, ts_ms) {
            Ok(id) => id,
            Err(ironrdp_egfx::server::FrameSubmissionError::Backpressured { .. }) => return Ok(None),
            Err(e) => return Err(format!("AVC420 submission refused: {e}")),
        };
        let msgs = s.drain_output();
        (frame_id, msgs, s.channel_id())
    };
    flush(gfx, msgs, channel)?;
    Ok(Some(frame_id))
}

fn flush(gfx: &Gfx, msgs: Vec<ironrdp_dvc::DvcMessage>, channel: Option<u32>) -> Result<(), String> {
    if msgs.is_empty() {
        return Ok(());
    }
    let channel = channel.ok_or("EGFX channel has no id")?;
    let svc = ironrdp_dvc::encode_dvc_messages(channel, msgs, ironrdp_svc::ChannelFlags::empty())
        .map_err(|e| format!("encode EGFX DVC messages: {e}"))?;
    let tx = gfx.sender.lock().expect("gfx").clone().ok_or("no server event sender")?;
    tx.send(ServerEvent::Egfx(ironrdp_server::EgfxServerMessage::SendMessages { messages: svc }))
        .map_err(|_| "server event loop gone".to_owned())
}

/// (Re)create the surface for w x h: ResetGraphics on resize, CreateSurface, MapSurfaceToOutput.
fn setup_surface(gfx: &Gfx, w: u32, h: u32, old: Option<&Surface>) -> Result<u16, String> {
    let handle = gfx.handle.lock().expect("gfx").clone().ok_or("no EGFX server")?;
    let (id, msgs, channel) = {
        let mut s = handle.lock().map_err(|_| "EGFX lock poisoned")?;
        let _ = old;
        // ResetGraphics with one primary monitor (FreeRDP refuses monitorCount 0), then a fresh surface
        let mon = ironrdp_pdu::gcc::Monitor {
            left: 0,
            top: 0,
            right: w as i32 - 1,
            bottom: h as i32 - 1,
            flags: ironrdp_pdu::gcc::MonitorFlags::PRIMARY,
        };
        s.resize_with_monitors(w as u16, h as u16, vec![mon]);
        let id = s.create_surface(w as u16, h as u16).ok_or("CreateSurface refused (EGFX not ready)")?;
        if !s.map_surface_to_output(id, 0, 0) {
            return Err("MapSurfaceToOutput failed".into());
        }
        (id, s.drain_output(), s.channel_id())
    };
    flush(gfx, msgs, channel)?;
    Ok(id)
}

fn run(p: &PipeParams, gfx: &Gfx, mailbox: &Mailbox, stats: &PipeStats) -> anyhow::Result<()> {
    let n = &p.native;
    let mut enc: Option<OpenH264> = None;
    let mut surface: Option<Surface> = None;
    let mut last: Option<Frame> = None; // last ENCODED frame
    let mut last_change = Instant::now();
    let mut refined = true;
    let mut aimd = Aimd::new(n.initial_bitrate_kbps * 1000, n.min_bitrate_kbps * 1000, n.max_bitrate_kbps * 1000);
    let t0 = Instant::now();
    let mut window_sent = 0u64;
    let mut window_bp = 0u64;
    let mut authed_at: Option<Instant> = None;
    let mut bitmap_mode = false;
    stats.bitrate_bps.store(u64::from(aimd.bps), Ordering::Relaxed);
    let mut want_idr = false;
    let mut codec_checked = false;

    while !p.stop.load(Ordering::SeqCst) {
        if !p.authenticated.load(Ordering::SeqCst) {
            authed_at = None;
            std::thread::sleep(Duration::from_millis(20));
            continue;
        }
        let authed = *authed_at.get_or_insert_with(Instant::now);
        if gfx.ready.load(Ordering::SeqCst) && !codec_checked {
            codec_checked = true;
            let h = gfx.handle.lock().expect("gfx").clone();
            let (a420, a444) = h.and_then(|h| h.lock().ok().map(|s| (s.supports_avc420(), s.supports_avc444()))).unwrap_or((false, false));
            gfx.avc420.store(a420, Ordering::SeqCst);
            gfx.avc444.store(a444, Ordering::SeqCst);
            info!(avc420 = a420, avc444 = a444, "EGFX codecs negotiated");
            if !a420 {
                warn!("client negotiated EGFX without AVC420: no H.264 is emitted");
            }
        }
        if !gfx.ready.load(Ordering::SeqCst) {
            codec_checked = false;
        }
        let ready = check_avc420_ready(gfx);
        if !ready {
            // no EGFX (or no AVC420) after egfx_wait: the classic bitmap path at a reduced rate
            let no_egfx = gfx.closed.load(Ordering::SeqCst) || authed.elapsed() > p.egfx_wait;
            if no_egfx && !bitmap_mode {
                bitmap_mode = true;
                stats.bitmap_mode.store(true, Ordering::Relaxed);
                warn!(
                    "client has no EGFX/AVC420 after {:?}: classic bitmap updates at {} frames/s (no H.264)",
                    p.egfx_wait, n.bitmap_hz
                );
                p.layout.store(1, Ordering::SeqCst);
                p.capture_hz.store(n.bitmap_hz.max(1), Ordering::SeqCst);
            }
            if bitmap_mode {
                match mailbox.take(Duration::from_millis(50)) {
                    Ok(Some(f)) if f.layout == PixelLayout::Bgra => {
                        stats.captured.fetch_add(1, Ordering::Relaxed);
                        if p.bitmap_tx.try_send(f).is_ok() {
                            stats.bitmap_frames.fetch_add(1, Ordering::Relaxed);
                        }
                    }
                    Ok(Some(f)) => mailbox.recycle(f.data),
                    Ok(None) => {}
                    Err(why) => anyhow::bail!("capture ended: {why}"),
                }
            } else {
                gfx.wait(Duration::from_millis(20));
            }
            continue;
        }
        if bitmap_mode {
            info!("EGFX became ready: leaving the bitmap path");
            bitmap_mode = false;
            stats.bitmap_mode.store(false, Ordering::Relaxed);
        }
        if p.layout.load(Ordering::Relaxed) != 0 {
            p.layout.store(0, Ordering::SeqCst);
            p.capture_hz.store(p.target_hz, Ordering::SeqCst);
        }
        if p.suppressed.load(Ordering::Relaxed) {
            // client minimised / screen off: no capture, no encode
            gfx.wait(Duration::from_millis(50));
            continue;
        }
        if p.refresh_request.swap(false, Ordering::SeqCst) {
            want_idr = true;
        }

        // backpressure: never encode a frame that cannot be sent (P-frames must go out in order)
        let (in_flight, max_in_flight) = {
            let h = gfx.handle.lock().expect("gfx").clone();
            h.and_then(|h| h.lock().ok().map(|s| (s.frames_in_flight(), s.max_frames_in_flight()))).unwrap_or((0, 1))
        };
        stats.frames_in_flight.store(in_flight, Ordering::Relaxed);
        if in_flight >= max_in_flight.max(1) {
            stats.backpressured.fetch_add(1, Ordering::Relaxed);
            window_bp += 1;
            gfx.wait(Duration::from_millis(30));
            continue;
        }

        let frame = match mailbox.take(Duration::from_millis(30)) {
            Ok(f) => f,
            Err(why) => anyhow::bail!("capture ended: {why}"),
        };
        let (frame, refine) = match frame {
            Some(f) if f.layout == PixelLayout::I420 => {
                stats.captured.fetch_add(1, Ordering::Relaxed);
                (f, false)
            }
            Some(f) => {
                mailbox.recycle(f.data);
                continue;
            }
            None => {
                // static refinement: once, 200 ms after the last change, re-encode the last frame in full
                if n.refine_static && !refined && last_change.elapsed() > Duration::from_millis(200) {
                    if let Some(l) = last.take() {
                        refined = true;
                        let copy = Frame { data: l.data.clone(), ready_at: Instant::now(), ..clone_meta(&l) };
                        last = Some(l);
                        (copy, true)
                    } else {
                        continue;
                    }
                } else {
                    continue;
                }
            }
        };

        let (w, h) = (frame.width, frame.height);
        // surface follows the capture size (DISP resize / rotation reaches us as a new buffer size)
        let cur_gen = gfx.generation.load(Ordering::SeqCst);
        let need_surface = match &surface {
            Some(s) => s.w != w || s.h != h || s.generation != cur_gen,
            None => true,
        };
        if need_surface {
            let same_conn = surface.as_ref().is_some_and(|s| s.generation == cur_gen);
            match setup_surface(gfx, w, h, if same_conn { surface.as_ref() } else { None }) {
                Ok(id) => {
                    info!(surface = id, w, h, "EGFX surface ready");
                    *stats.surface.lock().expect("stats") = (w as u16, h as u16);
                    surface = Some(Surface { id, w, h, generation: cur_gen });
                    last = None;
                    want_idr = true;
                }
                Err(e) => {
                    warn!("EGFX surface setup failed: {e}");
                    mailbox.recycle(frame.data);
                    gfx.wait(Duration::from_millis(100));
                    continue;
                }
            }
        }
        let sid = surface.as_ref().expect("surface").id;

        let regions = if want_idr || refine {
            full_rect(w, h)
        } else {
            match last.as_ref() {
                Some(l) if l.width == w && l.height == h && l.stride == frame.stride => {
                    match damage(&l.data, &frame.data, w, h, frame.stride, 16) {
                        Some((r, pct)) => {
                            stats.samples.lock().expect("stats").damage_pct.push(pct);
                            r
                        }
                        None => {
                            stats.skipped_static.fetch_add(1, Ordering::Relaxed);
                            mailbox.recycle(frame.data);
                            continue;
                        }
                    }
                }
                _ => {
                    want_idr = true;
                    full_rect(w, h)
                }
            }
        };

        let e = match enc.as_mut() {
            Some(e) => e,
            None => {
                let s = EncoderSettings {
                    width: w,
                    height: h,
                    fps: p.target_hz as f32,
                    bitrate_bps: aimd.bps,
                    max_bitrate_bps: n.max_bitrate_kbps * 1000,
                    qp_min: n.qp_min,
                    qp_max: n.qp_max,
                    threads: n.encoder_threads,
                    complexity: n.encoder_complexity.clone(),
                    full_range: n.color_matrix.ends_with("full"),
                };
                enc = Some(OpenH264::new(&n.openh264_library, s)?);
                enc.as_mut().expect("encoder")
            }
        };
        if want_idr {
            e.force_idr();
            want_idr = false;
        }
        let pic = I420 { data: &frame.data, width: w, height: h, stride: frame.stride };
        let ts = t0.elapsed().as_millis() as i64;
        let out = match e.encode(&pic, ts) {
            Ok(o) => o,
            Err(err) => {
                warn!("encode failed: {err:#}; resetting the encoder");
                enc = None;
                want_idr = true;
                mailbox.recycle(frame.data);
                continue;
            }
        };
        if out.skipped || out.data.is_empty() {
            stats.skipped_rc.fetch_add(1, Ordering::Relaxed);
            mailbox.recycle(frame.data);
            continue;
        }
        if out.idr {
            stats.idr.fetch_add(1, Ordering::Relaxed);
            if let Some(pl) = crate::encode::sps_profile_level(&out.data) {
                let mut l = stats.last_level.lock().expect("stats");
                if *l != Some(pl) {
                    info!(profile = pl.0, level = pl.1, "H.264 stream parameters");
                }
                *l = Some(pl);
            }
            stats.last_slices.store(crate::encode::slice_count(&out.data) as u32, Ordering::Relaxed);
        }
        match submit(gfx, sid, &out.data, &regions, ts as u32) {
            Ok(Some(id)) => {
                let sent = Instant::now();
                gfx.acks.lock().expect("gfx").sent.insert(id, (frame.ready_at, sent, out.data.len()));
                stats.encoded.fetch_add(1, Ordering::Relaxed);
                stats.bytes.fetch_add(out.data.len() as u64, Ordering::Relaxed);
                if refine {
                    stats.refined.fetch_add(1, Ordering::Relaxed);
                }
                window_sent += 1;
                let mut s = stats.samples.lock().expect("stats");
                s.encode_us.push(out.encode_us);
                s.convert_us.push(frame.convert_us);
                s.capture_to_send_us.push(sent.duration_since(frame.ready_at).as_micros() as u64);
                drop(s);
                if !refine {
                    last_change = Instant::now();
                    refined = false;
                    if let Some(old) = last.replace(frame) {
                        mailbox.recycle(old.data);
                    }
                } else {
                    mailbox.recycle(frame.data);
                }
            }
            Ok(None) => {
                // raced into backpressure after encoding: the encoder state moved on, so resync with an IDR
                stats.backpressured.fetch_add(1, Ordering::Relaxed);
                want_idr = true;
                mailbox.recycle(frame.data);
            }
            Err(err) => {
                debug!("{err}");
                mailbox.recycle(frame.data);
                gfx.wait(Duration::from_millis(50));
            }
        }

        let congested = {
            let a = gfx.acks.lock().expect("gfx");
            let recent: Vec<f64> = a.ack_ms.iter().rev().take(30).copied().collect();
            let slow = !recent.is_empty() && (crate::stats::median(&recent) > n.slow_ack_ms || crate::stats::median(&recent) > 90.0);
            slow || (window_sent > 0 && window_bp * 10 > window_sent)
        };
        if let Some(bps) = aimd.tick(congested, Instant::now()) {
            if let Some(e) = enc.as_mut() {
                e.set_bitrate(bps);
            }
            stats.bitrate_bps.store(u64::from(bps), Ordering::Relaxed);
            window_sent = 0;
            window_bp = 0;
        }
    }
    if let Some(l) = last.take() {
        mailbox.recycle(l.data);
    }
    Ok(())
}

fn clone_meta(f: &Frame) -> Frame {
    Frame {
        seq: f.seq,
        width: f.width,
        height: f.height,
        stride: f.stride,
        layout: f.layout,
        data: Vec::new(),
        ready_at: f.ready_at,
        capture_us: 0,
        convert_us: 0,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pic(w: u32, h: u32) -> (Vec<u8>, u32) {
        let s = w.div_ceil(16) * 16;
        (vec![0u8; (s * h * 3 / 2) as usize], s)
    }

    #[test]
    fn identical_frames_have_no_damage() {
        let (a, s) = pic(1100, 2080);
        assert!(damage(&a, &a.clone(), 1100, 2080, s, 16).is_none());
    }

    #[test]
    fn one_pixel_change_damages_one_macroblock() {
        let (a, s) = pic(1100, 2080);
        let mut b = a.clone();
        b[(s * 100 + 500) as usize] = 7;
        let (r, pct) = damage(&a, &b, 1100, 2080, s, 16).expect("damage");
        assert_eq!(r.len(), 1);
        assert_eq!((r[0].left, r[0].top, r[0].right, r[0].bottom), (496, 96, 512, 112));
        assert!(pct < 0.2);
    }

    #[test]
    fn chroma_only_change_is_seen() {
        let (a, s) = pic(64, 64);
        let mut b = a.clone();
        let v = (s * 64 + s / 2 + 20) as usize; // V plane, row 0, x 20 -> luma x 40
        b[v] = 99;
        let (r, _) = damage(&a, &b, 64, 64, s, 16).expect("damage");
        assert_eq!((r[0].left, r[0].top, r[0].right, r[0].bottom), (32, 0, 48, 16));
    }

    #[test]
    fn rects_are_capped_and_clipped() {
        let (a, s) = pic(1100, 2080);
        let mut b = a.clone();
        for y in (0..2080).step_by(32) {
            b[(s * y + 1099) as usize] = 1; // right edge, every other MB row
        }
        let (r, _) = damage(&a, &b, 1100, 2080, s, 16).expect("damage");
        assert!(r.len() <= 16, "{}", r.len());
        assert!(r.iter().all(|x| x.right <= 1100 && x.bottom <= 2080));
    }

    #[test]
    fn aimd_backs_off_and_recovers() {
        let mut a = Aimd::new(8_000_000, 1_500_000, 25_000_000);
        let t = Instant::now() + Duration::from_secs(2);
        assert_eq!(a.tick(true, t), Some(6_800_000));
        assert_eq!(a.tick(false, t + Duration::from_secs(1)), Some(7_140_000));
        // never below min
        let mut b = Aimd::new(1_600_000, 1_500_000, 25_000_000);
        assert_eq!(b.tick(true, t), Some(1_500_000));
    }

    #[test]
    fn ready_condition_requires_both_gfx_ready_and_avc420() {
        let gfx = Gfx::default();
        assert!(!check_avc420_ready(&gfx));

        gfx.ready.store(true, Ordering::SeqCst);
        assert!(!check_avc420_ready(&gfx));

        gfx.ready.store(false, Ordering::SeqCst);
        gfx.avc420.store(true, Ordering::SeqCst);
        assert!(!check_avc420_ready(&gfx));

        gfx.ready.store(true, Ordering::SeqCst);
        gfx.avc420.store(true, Ordering::SeqCst);
        assert!(check_avc420_ready(&gfx), "pipeline must be ready when both gfx.ready and gfx.avc420 are true");
    }
}

pub fn check_avc420_ready(gfx: &Gfx) -> bool {
    if true {
        let ready = gfx.ready.load(Ordering::SeqCst) && gfx.avc420.load(Ordering::SeqCst);
        return ready;
    }
    false
}
