//! EGFX factory/handler and newest-frame encoding: I420 -> OpenH264 AVC420,
//! or BGRA -> independent damaged RemoteFX Progressive tiles for AVC-disabled
//! clients. Both use the negotiated DVC and bounded frame-ack window. Damage
//! always references the last submitted frame. Delivered EGFX frames/s = acks/s.

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
    pub progressive: AtomicBool,
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
        self.progressive.store(false, Ordering::SeqCst);
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

/// Latest-frame handoff for the classic bitmap path: the pipeline thread offers the newest frame,
/// the connection's update loop takes it. A slow client can never build a backlog here: an offer
/// displaces (returns) the previously pending frame, so at most one frame is ever queued and it is
/// always the newest one. Delivery is counted at the consumer side, not here.
#[derive(Default)]
pub struct BitmapSlot {
    frame: Mutex<Option<Frame>>,
    notify: tokio::sync::Notify,
}

impl BitmapSlot {
    /// Offer the newest frame; `Some(_)` = the displaced stale frame (recycle its buffer).
    pub fn offer(&self, f: Frame) -> Option<Frame> {
        let old = self.frame.lock().expect("bitmap slot").replace(f);
        self.notify.notify_one();
        old
    }
    /// Synchronously steal the pending frame (connection teardown / fresh connection).
    pub fn take_pending(&self) -> Option<Frame> {
        self.frame.lock().expect("bitmap slot").take()
    }
    /// Wait for the newest frame. `notify_one` leaves a permit when nobody waits, so an offer
    /// racing the empty-check is never lost.
    pub async fn take(&self) -> Frame {
        loop {
            if let Some(f) = self.frame.lock().expect("bitmap slot").take() {
                return f;
            }
            self.notify.notified().await;
        }
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

/// Suppress sub-threshold desktop noise against the last TRANSMITTED bitmap,
/// never against the latest captured/dropped frame. Small cumulative motion
/// eventually crosses the threshold; text/cursor changes pass unchanged. The
/// compositor's rendering, dimensions and input coordinates are untouched.
pub fn stabilize_bitmap(data: &mut [u8], reference: &[u8], width: usize, height: usize, stride: usize, threshold: u8) -> bool {
    let Some(row) = width.checked_mul(4) else { return true; };
    let Some(needed) = height.checked_mul(stride) else { return true; };
    if row > stride || data.len() < needed || reference.len() < needed { return true; }
    let mut changed=false;
    for y in 0..height {
        for x in 0..width {
            let off=y*stride+x*4;
            if (0..3).any(|c| data[off+c].abs_diff(reference[off+c]) > threshold) {
                changed=true;
            } else {
                data[off..off+4].copy_from_slice(&reference[off..off+4]);
            }
        }
    }
    changed
}

/// Service time of the bitmap handed to IronRDP. Its display loop requests the
/// next update only AFTER encoding and writing all fragments of this one. A
/// newest-frame slot deliberately hides backlog age, so it cannot measure this.
#[derive(Default)]
pub struct BitmapWrite {
    pending: Option<(Instant, Instant)>,
}
impl BitmapWrite {
    pub fn begin(&mut self, ready: Instant, now: Instant) { self.pending = Some((ready, now)); }
    pub fn age_us(&self, now: Instant) -> u64 {
        self.pending.map_or(0, |(_, start)| now.saturating_duration_since(start).as_micros().min(u128::from(u64::MAX)) as u64)
    }
    pub fn complete(&mut self, now: Instant) -> Option<(u64,u64)> {
        self.pending.take().map(|(ready,start)| (
            now.saturating_duration_since(start).as_micros().min(u128::from(u64::MAX)) as u64,
            now.saturating_duration_since(ready).as_micros().min(u128::from(u64::MAX)) as u64))
    }
    pub fn clear(&mut self) { self.pending = None; }
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
    pub bitmap_delivered: AtomicU64,
    pub bitmap_write: Mutex<BitmapWrite>,
    pub bitmap_stale_dropped: AtomicU64,
    pub bitmap_mode: AtomicBool,
    pub surface: Mutex<(u16, u16)>,
}

#[derive(Default)]
pub struct Samples {
    pub encode_us: Vec<u64>,
    pub convert_us: Vec<u64>,
    pub capture_to_send_us: Vec<u64>,
    pub bitmap_write_us: Vec<u64>,
    pub bitmap_capture_to_write_us: Vec<u64>,
    pub damage_pct: Vec<f64>,
}

pub struct PipeParams {
    pub native: Native,
    pub target_hz: u32,
    pub bitmap: Arc<BitmapSlot>,
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

/// Adaptive bitmap capture rate: the bitmap path has no frame acks, so the queue age of delivered
/// frames (capture ready -> handed to the connection) is the congestion signal. One step per stats
/// window: -20 % while the p50 queue age is above `slow_us`, +1 Hz once it is below half of it
/// (hysteresis), bounded to [floor, ceil]. A degenerate `slow_us` = 0 disables stepping entirely.
pub fn bitmap_pace(cur_hz: u32, p50_us: u64, slow_us: u64, floor: u32, ceil: u32) -> u32 {
    let ceil = ceil.max(1);
    let floor = floor.clamp(1, ceil);
    let cur = cur_hz.clamp(floor, ceil);
    if slow_us > 0 && p50_us > slow_us {
        ((f64::from(cur) * 0.8) as u32).max(floor)
    } else if p50_us < slow_us / 2 && cur < ceil {
        cur + 1
    } else {
        cur
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

/// Only the old V8/V8.1 THIN_CLIENT flag forbids Progressive. AVC_THIN_CLIENT
/// on newer capability versions concerns AVC444, not the wavelet codec.
fn supports_progressive(cap: &CapabilitySet) -> bool {
    use ironrdp_egfx::pdu::{CapabilitiesV8Flags, CapabilitiesV81Flags};
    match cap {
        CapabilitySet::V8 { flags } => !flags.contains(CapabilitiesV8Flags::THIN_CLIENT),
        CapabilitySet::V8_1 { flags } => !flags.contains(CapabilitiesV81Flags::THIN_CLIENT),
        _ => true,
    }
}

/// Encode independent 64x64 TILE_SIMPLE blocks. Damage is compared with the
/// last successfully submitted frame; dropped captures never become references.
/// Edge pixels are replicated so partial tiles do not introduce dark borders.
fn encode_progressive(frame: &Frame, previous: Option<&Frame>, index: u32, quant: u8) -> anyhow::Result<Option<Vec<u8>>> {
    use ironrdp_graphics::progressive::{encode_first_pass, rgba_to_ycbcr};
    use ironrdp_pdu::codecs::rfx::RfxRectangle;
    use ironrdp_pdu::codecs::rfx::progressive::*;
    let (w, h, stride) = (frame.width as usize, frame.height as usize, frame.stride as usize);
    anyhow::ensure!(frame.layout == PixelLayout::Bgra && w > 0 && h > 0 && w <= u16::MAX as usize && h <= u16::MAX as usize
        && stride >= w * 4 && frame.data.len() >= stride * h, "invalid Progressive BGRA frame");
    anyhow::ensure!((6..=15).contains(&quant), "invalid Progressive quantization");
    anyhow::ensure!(w.div_ceil(64) * h.div_ceil(64) <= u16::MAX as usize, "too many Progressive tiles");
    let previous = previous.filter(|p| p.width == frame.width && p.height == frame.height && p.stride == frame.stride
        && p.layout == PixelLayout::Bgra && p.data.len() >= stride * h);
    let base = ComponentCodecQuant { ll3: quant, hl3: quant, lh3: quant, hh3: quant,
        hl2: quant, lh2: quant, hh2: quant, hl1: quant, lh1: quant, hh1: quant };
    let mut payloads = Vec::new();
    let mut rgba = vec![0u8; 64 * 64 * 4];
    let mut components = [[0i16; 4096]; 3];
    // Reuse the entropy scratch buffer. Retaining its capacity per tile would
    // consume over 100 MB for a 1100x2080 desktop even when it encodes to 58 KB.
    let mut entropy = vec![0u8; 65536];
    for y in (0..h).step_by(64) {
        for x in (0..w).step_by(64) {
            let (tw, th) = ((w - x).min(64), (h - y).min(64));
            if previous.is_some_and(|p| (y..y + th).all(|row| {
                let off = row * stride + x * 4;
                frame.data[off..off + tw * 4] == p.data[off..off + tw * 4]
            })) { continue; }
            for ty in 0..64 {
                for tx in 0..64 {
                    let src = (y + ty.min(th - 1)) * stride + (x + tx.min(tw - 1)) * 4;
                    let dst = (ty * 64 + tx) * 4;
                    rgba[dst..dst + 4].copy_from_slice(&[frame.data[src + 2], frame.data[src + 1], frame.data[src], 255]);
                }
            }
            let [yc, cb, cr] = &mut components;
            rgba_to_ycbcr(&rgba, yc, cb, cr);
            let mut planes: [Vec<u8>; 3] = std::array::from_fn(|_| Vec::new());
            for (component, output) in components.iter_mut().zip(&mut planes) {
                let len = encode_first_pass(component, &mut entropy, &base, &ComponentCodecQuant::LOSSLESS, false)
                    .map_err(|e| anyhow::anyhow!("Progressive tile encoding: {e:?}"))?;
                *output = entropy[..len].to_vec();
            }
            payloads.push((x as u16 / 64, y as u16 / 64, planes));
        }
    }
    if payloads.is_empty() { return Ok(None); }
    let tiles = payloads.iter().map(|(x, y, p)| ProgressiveTile::Simple(TileSimple {
        quant_idx_y: 0, quant_idx_cb: 0, quant_idx_cr: 0, x_idx: *x, y_idx: *y,
        flags: 0, y_data: &p[0], cb_data: &p[1], cr_data: &p[2], tail_data: &[],
    })).collect();
    let region = ProgressiveRegion { tile_size: 64, rects: vec![RfxRectangle { x: 0, y: 0, width: w as u16, height: h as u16 }],
        quant_vals: vec![base], quant_prog_vals: vec![], flags: 0, tiles };
    let mut blocks = Vec::new();
    if index == 0 {
        blocks.push(ProgressiveBlock::Sync(ProgressiveSyncPdu));
        blocks.push(ProgressiveBlock::Context(ProgressiveContextPdu { context_id: 0, tile_size: 64, flags: 0 }));
    }
    blocks.extend([ProgressiveBlock::FrameBegin(ProgressiveFrameBeginPdu { frame_index: index, region_count: 1 }),
        ProgressiveBlock::Region(region), ProgressiveBlock::FrameEnd(ProgressiveFrameEndPdu)]);
    Ok(Some(encode_progressive_stream(&blocks).map_err(|e| anyhow::anyhow!("Progressive bitmap stream: {e}"))?))
}

fn submit_progressive(gfx: &Gfx, surface: u16, data: Vec<u8>, ts_ms: u32) -> Result<Option<u32>, String> {
    let handle = gfx.handle.lock().expect("gfx").clone().ok_or("no EGFX server")?;
    let (frame_id, msgs, channel) = {
        let mut s = handle.lock().map_err(|_| "EGFX lock poisoned")?;
        let Some(frame_id) = s.send_remotefx_progressive_frame(surface, 0, data, ts_ms) else { return Ok(None); };
        (frame_id, s.drain_output(), s.channel_id())
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
    let mut progressive_index = 0u32;

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
            let (a420, a444, progressive) = h.and_then(|h| h.lock().ok().map(|s| (s.supports_avc420(), s.supports_avc444(),
                n.progressive && s.negotiated_capabilities().is_some_and(supports_progressive)))).unwrap_or((false, false, false));
            gfx.avc420.store(a420, Ordering::SeqCst);
            gfx.avc444.store(a444, Ordering::SeqCst);
            gfx.progressive.store(!a420 && progressive, Ordering::SeqCst);
            info!(avc420 = a420, avc444 = a444, progressive = !a420 && progressive, "EGFX codecs negotiated");
            if !a420 {
                info!(progressive, "client disabled AVC420: H.264 will not be emitted");
            }
        }
        if !gfx.ready.load(Ordering::SeqCst) {
            codec_checked = false;
        }
        let ready = check_egfx_ready(gfx);
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
                        if let Some(stale) = p.bitmap.offer(f) {
                            // the client had not drained the previous frame: keep the newest, recycle the stale one
                            stats.bitmap_stale_dropped.fetch_add(1, Ordering::Relaxed);
                            mailbox.recycle(stale.data);
                        } else {
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
        }
        // PipeStats outlives an individual connection. A prior bitmap-only
        // client must not leave the next EGFX client in bitmap pacing/reporting.
        stats.bitmap_mode.store(false, Ordering::Relaxed);
        let progressive = gfx.progressive.load(Ordering::SeqCst);
        let layout = u32::from(progressive);
        if p.layout.load(Ordering::Relaxed) != layout {
            p.layout.store(layout, Ordering::SeqCst);
        }
        p.capture_hz.store(if progressive { n.progressive_hz.min(p.target_hz).max(1) } else { p.target_hz }, Ordering::SeqCst);
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
        let (mut frame, refine) = match frame {
            Some(f) if f.layout == if progressive { PixelLayout::Bgra } else { PixelLayout::I420 } => {
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
                    progressive_index = 0;
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

        if progressive {
            if let Some(l) = last.as_ref().filter(|l| l.width == w && l.height == h && l.stride == frame.stride) {
                stabilize_bitmap(&mut frame.data, &l.data, w as usize, h as usize, frame.stride as usize, n.bitmap_damage_threshold);
            }
            let start = Instant::now();
            let data = match encode_progressive(&frame, if want_idr || refine { None } else { last.as_ref() }, progressive_index, n.progressive_quant) {
                Ok(data) => data,
                Err(err) => {
                    warn!("Progressive encoding refused: {err}; reverting this connection to bitmap updates");
                    gfx.progressive.store(false, Ordering::SeqCst);
                    mailbox.recycle(frame.data);
                    continue;
                }
            };
            let Some(data) = data else {
                stats.skipped_static.fetch_add(1, Ordering::Relaxed);
                mailbox.recycle(frame.data);
                continue;
            };
            let bytes = data.len();
            match submit_progressive(gfx, sid, data, t0.elapsed().as_millis() as u32) {
                Ok(Some(id)) => {
                    let sent = Instant::now();
                    gfx.acks.lock().expect("gfx").sent.insert(id, (frame.ready_at, sent, bytes));
                    stats.encoded.fetch_add(1, Ordering::Relaxed);
                    stats.bytes.fetch_add(bytes as u64, Ordering::Relaxed);
                    let mut samples = stats.samples.lock().expect("stats");
                    samples.encode_us.push(start.elapsed().as_micros() as u64);
                    samples.convert_us.push(frame.convert_us);
                    samples.capture_to_send_us.push(sent.saturating_duration_since(frame.ready_at).as_micros() as u64);
                    drop(samples);
                    progressive_index = progressive_index.wrapping_add(1);
                    want_idr = progressive_index == 0;
                    if let Some(old) = last.replace(frame) { mailbox.recycle(old.data); }
                    // TILE_SIMPLE already carries the complete quantized tile; no
                    // additional identical static refinement needs to be sent.
                    refined = true;
                }
                Ok(None) => {
                    stats.backpressured.fetch_add(1, Ordering::Relaxed);
                    mailbox.recycle(frame.data);
                }
                Err(err) => {
                    warn!("Progressive submission failed: {err}");
                    mailbox.recycle(frame.data);
                    gfx.wait(Duration::from_millis(50));
                }
            }
            continue;
        }

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

    fn progressive_picture(w: u32, h: u32) -> Frame {
        let mut data = vec![0; (w * h * 4) as usize];
        for p in data.chunks_exact_mut(4) { p.copy_from_slice(&[192, 128, 64, 255]); }
        Frame { seq: 1, width: w, height: h, stride: w * 4, layout: PixelLayout::Bgra,
            data, ready_at: Instant::now(), capture_us: 0, convert_us: 0 }
    }

    #[test]
    fn progressive_selection_keeps_avc_disabled_clients_on_egfx_but_respects_old_thin_clients() {
        use ironrdp_egfx::pdu::{CapabilitiesV8Flags, CapabilitiesV81Flags, CapabilitiesV104Flags};
        assert!(supports_progressive(&CapabilitySet::V10_6 { flags: CapabilitiesV104Flags::AVC_DISABLED }));
        assert!(supports_progressive(&CapabilitySet::V10_6 { flags: CapabilitiesV104Flags::AVC_DISABLED | CapabilitiesV104Flags::AVC_THIN_CLIENT }));
        assert!(!supports_progressive(&CapabilitySet::V8 { flags: CapabilitiesV8Flags::THIN_CLIENT }));
        assert!(!supports_progressive(&CapabilitySet::V8_1 { flags: CapabilitiesV81Flags::THIN_CLIENT }));
        assert!(supports_progressive(&CapabilitySet::V8 { flags: CapabilitiesV8Flags::SMALL_CACHE }));
        let gfx = Gfx::default();
        gfx.progressive.store(true, Ordering::SeqCst);
        assert!(!check_egfx_ready(&gfx));
        gfx.ready.store(true, Ordering::SeqCst);
        assert!(check_egfx_ready(&gfx));
        assert!(!check_avc420_ready(&gfx), "AVC disabled still must never emit H.264");
        gfx.reset();
        assert!(!check_egfx_ready(&gfx));
    }

    #[test]
    fn progressive_roundtrip_partial_tiles_and_rgb_channels() {
        let f = progressive_picture(70, 66);
        let data = encode_progressive(&f, None, 0, 8).unwrap().unwrap();
        assert!(data.len() < f.data.len() / 8, "a flat desktop must compress");
        let mut decoder = ironrdp_graphics::progressive::ProgressiveDecoder::new();
        let decoded = decoder.decode_bitmap(1, 0, 70, 66, &data).expect("decode actual encoded blocks");
        assert_eq!(decoded.len(), 4);
        assert!(decoded.iter().any(|t| t.x_idx == 1 && t.y_idx == 1));
        for tile in decoded {
            for pixel in tile.pixels.chunks_exact(4) {
                for (got, expected) in pixel[..3].iter().zip([64i16, 128, 192]) {
                    assert!((i16::from(*got) - expected).abs() <= 4, "BGRA/RGBA or wavelet mismatch: {pixel:?}");
                }
            }
            assert!(tile.update_rectangles.iter().all(|r| r.right <= 70 && r.bottom <= 66));
        }
        assert!(decoder.decode_bitmap(1, 0, 70, 66, &data[..data.len() / 2]).is_err(), "truncated REGION payload must fail");
    }

    #[test]
    fn progressive_damage_uses_submitted_reference_and_retains_context() {
        let f = progressive_picture(128, 64);
        let mut decoder = ironrdp_graphics::progressive::ProgressiveDecoder::new();
        let first = encode_progressive(&f, None, 0, 6).unwrap().unwrap();
        assert_eq!(decoder.decode_bitmap(2, 0, 128, 64, &first).unwrap().len(), 2);
        assert!(encode_progressive(&f, Some(&f), 1, 6).unwrap().is_none());
        let mut moved = progressive_picture(128, 64);
        moved.data[(20 * 128 + 100) * 4 + 2] = 255;
        // A capture can be dropped; the reference is still f until submit succeeds.
        let pending = encode_progressive(&moved, Some(&f), 1, 6).unwrap().unwrap();
        assert_eq!(pending, encode_progressive(&moved, Some(&f), 1, 6).unwrap().unwrap());
        let changed = decoder.decode_bitmap(2, 0, 128, 64, &pending).unwrap();
        assert_eq!(changed.len(), 1);
        assert_eq!((changed[0].x_idx, changed[0].y_idx), (1, 0));
        let rgba = &changed[0].pixels[(20 * 64 + 36) * 4..][..4];
        assert!(rgba[0] > 220, "one-pixel red change must survive: {rgba:?}");
        let resized = progressive_picture(64, 64);
        let refresh = encode_progressive(&resized, Some(&f), 0, 6).unwrap().unwrap();
        assert_eq!(decoder.decode_bitmap(3, 0, 64, 64, &refresh).unwrap().len(), 1);
        let mut bad = progressive_picture(64, 64);
        bad.stride = 4;
        assert!(encode_progressive(&bad, None, 0, 8).is_err());
        assert!(encode_progressive(&f, None, 0, 16).is_err());
    }

    #[test]
    fn progressive_real_dvc_negotiation_enforces_frame_ack_backpressure() {
        use ironrdp_dvc::DvcProcessor;
        use ironrdp_egfx::pdu::{CapabilitiesV104Flags, FrameAcknowledgePdu, GfxPdu, QueueDepth};
        let gfx = Arc::new(Gfx::default());
        let mut server = GraphicsPipelineServer::new(Box::new(Handler { gfx: Arc::clone(&gfx), max_in_flight: 1 }));
        server.start(7).unwrap();
        let cap = CapabilitySet::V10_6 { flags: CapabilitiesV104Flags::AVC_DISABLED };
        let caps = GfxPdu::CapabilitiesAdvertise(CapabilitiesAdvertisePdu(vec![(&cap).into()]));
        server.process(7, &ironrdp_core::encode_vec(&caps).unwrap()).unwrap();
        assert!(server.is_ready());
        assert!(!server.supports_avc420());
        assert!(supports_progressive(server.negotiated_capabilities().unwrap()));
        *gfx.handle.lock().unwrap() = Some(Arc::new(Mutex::new(server)));
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
        *gfx.sender.lock().unwrap() = Some(tx);
        let sid = setup_surface(&gfx, 64, 64, None).unwrap();
        assert!(rx.try_recv().is_ok(), "surface PDUs must reach event loop");
        let data = encode_progressive(&progressive_picture(64, 64), None, 0, 8).unwrap().unwrap();
        let first = submit_progressive(&gfx, sid, data.clone(), 0).unwrap().unwrap();
        assert!(rx.try_recv().is_ok(), "encoded frame must reach DVC writer");
        assert_eq!(submit_progressive(&gfx, sid, data.clone(), 1).unwrap(), None, "full ACK window must refuse another frame");
        assert!(rx.try_recv().is_err(), "backpressure must emit no queued frame");
        let handle = gfx.handle.lock().unwrap().clone().unwrap();
        let ack = GfxPdu::FrameAcknowledge(FrameAcknowledgePdu { frame_id: first, queue_depth: QueueDepth::Unavailable, total_frames_decoded: 1 });
        handle.lock().unwrap().process(7, &ironrdp_core::encode_vec(&ack).unwrap()).unwrap();
        assert!(submit_progressive(&gfx, sid, data, 2).unwrap().is_some(), "ACK must reopen window");
    }

    #[test]
    fn progressive_pipeline_clears_previous_connection_bitmap_state() {
        use ironrdp_dvc::DvcProcessor;
        use ironrdp_egfx::pdu::{CapabilitiesV104Flags, GfxPdu};
        let gfx = Arc::new(Gfx::default());
        let mut server = GraphicsPipelineServer::new(Box::new(Handler { gfx: Arc::clone(&gfx), max_in_flight: 1 }));
        server.start(8).unwrap();
        let cap=CapabilitySet::V10_6 { flags: CapabilitiesV104Flags::AVC_DISABLED };
        let caps=GfxPdu::CapabilitiesAdvertise(CapabilitiesAdvertisePdu(vec![(&cap).into()]));
        server.process(8,&ironrdp_core::encode_vec(&caps).unwrap()).unwrap();
        *gfx.handle.lock().unwrap()=Some(Arc::new(Mutex::new(server)));
        let (tx,mut rx)=tokio::sync::mpsc::unbounded_channel();
        *gfx.sender.lock().unwrap()=Some(tx);
        let stats=Arc::new(PipeStats::default());
        stats.bitmap_mode.store(true,Ordering::SeqCst);
        let mailbox=Arc::new(Mailbox::default());
        mailbox.put(progressive_picture(64,64));
        let stop=Arc::new(AtomicBool::new(false));
        let thread=spawn(PipeParams {
            native:Native::default(),target_hz:30,bitmap:Arc::new(BitmapSlot::default()),
            layout:Arc::new(AtomicU32::new(1)),capture_hz:Arc::new(AtomicU32::new(30)),
            authenticated:Arc::new(AtomicBool::new(true)),suppressed:Arc::new(AtomicBool::new(false)),
            stop:Arc::clone(&stop),refresh_request:Arc::new(AtomicBool::new(false)),egfx_wait:Duration::ZERO,
        },Arc::clone(&gfx),Arc::clone(&mailbox),Arc::clone(&stats));
        let deadline=Instant::now()+Duration::from_secs(2);
        while stats.encoded.load(Ordering::SeqCst)==0 && Instant::now()<deadline { std::thread::sleep(Duration::from_millis(2)); }
        stop.store(true,Ordering::SeqCst);thread.join().unwrap();
        assert_eq!(stats.encoded.load(Ordering::SeqCst),1,"actual encode thread must submit the new client's frame");
        assert!(stats.bytes.load(Ordering::SeqCst)>0);
        assert!(gfx.progressive.load(Ordering::SeqCst));
        assert!(!stats.bitmap_mode.load(Ordering::SeqCst),"previous connection must not impose bitmap pacing or codec reporting");
        assert!(rx.try_recv().is_ok());
    }

    #[test]
    #[ignore = "requires the recorded mobile BGRA fixture; reports compression/encode cost, not phone FPS"]
    fn progressive_recorded_mobile_frame_compression() {
        let path = std::env::var("MIOS_BITMAP_AFTER").expect("fixture path");
        let data = std::fs::read(path).unwrap();
        assert_eq!(data.len(), 1100 * 2080 * 4);
        let f = Frame { data, ..progressive_picture(1100, 2080) };
        let start = Instant::now();
        let encoded = encode_progressive(&f, None, 0, 8).unwrap().unwrap();
        let ms = start.elapsed().as_secs_f64() * 1000.0;
        let mut decoder = ironrdp_graphics::progressive::ProgressiveDecoder::new();
        let tiles = decoder.decode_bitmap(0, 0, 1100, 2080, &encoded).unwrap();
        assert_eq!(tiles.len(), 1100usize.div_ceil(64) * 2080usize.div_ceil(64));
        println!("Progressive mobile full frame: {} bytes, {:.1} ms encode, {} decoded tiles; no phone throughput claim", encoded.len(), ms, tiles.len());
    }

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

    fn bframe(seq: u64) -> Frame {
        Frame { seq, width: 4, height: 4, stride: 4, layout: PixelLayout::Bgra, data: vec![0u8; 64], ready_at: Instant::now(), capture_us: 0, convert_us: 0 }
    }

    #[test]
    fn bitmap_slot_displaces_stale_and_keeps_newest() {
        let slot = BitmapSlot::default();
        assert!(slot.offer(bframe(1)).is_none(), "the first offer displaces nothing");
        for seq in 2..=50u64 {
            let stale = slot.offer(bframe(seq)).expect("the previous frame must be displaced");
            assert_eq!(stale.seq, seq - 1);
        }
        let taken = tokio::runtime::Builder::new_current_thread().build().expect("rt").block_on(slot.take());
        assert_eq!(taken.seq, 50, "the newest frame must survive the pressure");
        assert!(slot.take_pending().is_none(), "exactly one buffer is ever retained");
    }

    #[test]
    fn bitmap_slot_never_grows_under_a_stalled_client() {
        // negative control: a client that never drains the slot must not accumulate a backlog
        let slot = BitmapSlot::default();
        for seq in 0..1000u64 {
            let _ = slot.offer(bframe(seq));
        }
        assert_eq!(slot.take_pending().expect("one retained frame").seq, 999);
        assert!(slot.take_pending().is_none());
    }

    #[test]
    #[ignore = "requires the explicit recorded 1100x2080 mobile background fixture pair"]
    fn bitmap_live_background_fixture_removes_noise_not_real_pixels() {
        let before=std::fs::read(std::env::var("MIOS_BITMAP_BEFORE").expect("fixture baseline")).unwrap();
        let mut after=std::fs::read(std::env::var("MIOS_BITMAP_AFTER").expect("fixture current")).unwrap();
        let original=after.clone();let (w,h)=(1100usize,2080usize);
        assert_eq!(before.len(),w*h*4);assert_eq!(after.len(),before.len());
        let dirty=|a:&[u8]|a.chunks_exact(4).zip(before.chunks_exact(4)).filter(|(a,b)|a[..3]!=b[..3]).count();
        let noisy=dirty(&after);let threshold=crate::config::Native::default().bitmap_damage_threshold;
        assert!(stabilize_bitmap(&mut after,&before,w,h,w*4,threshold));
        let retained=dirty(&after);assert!(noisy>w*h/2 && retained<noisy/10,"fixture must demonstrate the reported background flood");
        for ((old,new),sent) in before.chunks_exact(4).zip(original.chunks_exact(4)).zip(after.chunks_exact(4)) {
            if (0..3).any(|c|old[c].abs_diff(new[c])>threshold) {assert_eq!(sent,new,"significant pixels retain exact RGB");}
        }
        println!("mobile background pixels: {noisy} dirty -> {retained} significant; threshold={threshold}; this is damage reduction, not a measured phone FPS");
    }

    #[test]
    fn bitmap_noise_filter_uses_the_transmitted_baseline() {
        let reference=vec![100u8,100,100,255, 10,20,30,255];
        let mut noise=vec![102u8,99,101,0, 12,18,31,255];
        assert!(!stabilize_bitmap(&mut noise,&reference,2,1,8,3));
        assert_eq!(noise,reference,"minor noise must not dirty the whole desktop");
        // Compare a later capture to the SAME reference, not the previous
        // discarded capture: slow colour/position changes must remain visible.
        let mut later=vec![104u8,100,100,255, 10,20,30,255];
        assert!(stabilize_bitmap(&mut later,&reference,2,1,8,3));
        assert_eq!(later[0],104);
        let mut text=vec![255u8,255,255,255, 10,20,30,255];
        assert!(stabilize_bitmap(&mut text,&reference,2,1,8,3));
        assert_eq!(text[0],255,"preserve significant text/cursor pixels exactly");
    }
    #[test]
    fn bitmap_noise_filter_exact_mode_stride_and_invalid_buffers() {
        let reference=vec![100u8,100,100,255, 9,9,9,9];
        let mut data=vec![101u8,100,100,255, 8,8,8,8];
        assert!(stabilize_bitmap(&mut data,&reference,1,1,8,0));
        assert_eq!(&data[4..],&[8,8,8,8],"row padding is not image data");
        assert!(stabilize_bitmap(&mut data,&reference,3,1,8,3));
        assert!(stabilize_bitmap(&mut data,&reference,1,3,8,3));
    }

    #[test]
    fn bitmap_write_pressure_survives_a_fresh_latest_frame() {
        let start=Instant::now();let mut write=BitmapWrite::default();
        assert_eq!(write.complete(start),None,"no completed frame may be invented");
        write.begin(start-Duration::from_millis(12),start);
        let stalled=start+Duration::from_secs(2);
        // Slot age is only 1 ms after replacing stale frames: the old pacing
        // signal incorrectly increases/holds the rate during this stalled write.
        assert_eq!(bitmap_pace(15,1_000,250_000,2,15),15);
        assert!(bitmap_pace(15,write.age_us(stalled),250_000,2,15)<15);
        assert_eq!(write.complete(stalled),Some((2_000_000,2_012_000)));
        assert_eq!(write.complete(stalled),None,"count each completed socket write once");
        assert_eq!(write.age_us(stalled),0);
    }
    #[test]
    fn bitmap_write_disconnect_and_fast_delivery_controls() {
        let start=Instant::now();let mut write=BitmapWrite::default();
        write.begin(start,start);write.clear();
        assert_eq!(write.complete(start+Duration::from_secs(9)),None,"disconnect is not a delivered frame");
        write.begin(start,start);
        let (service,_) = write.complete(start+Duration::from_millis(5)).unwrap();
        assert_eq!(bitmap_pace(7,service,250_000,2,15),8,"healthy path recovers normally");
    }

    #[test]
    fn bitmap_pace_backs_off_under_queue_pressure_and_recovers_slowly() {
        let slow = 250_000u64;
        let mut hz = 15u32;
        for _ in 0..20 {
            hz = bitmap_pace(hz, slow + 1, slow, 2, 15);
        }
        assert_eq!(hz, 2, "must fall to the floor, never below it");
        let mut hz = 2u32;
        for _ in 0..20 {
            hz = bitmap_pace(hz, slow / 4, slow, 2, 15);
        }
        assert_eq!(hz, 15, "must climb back to the ceiling, never above it");
        assert_eq!(bitmap_pace(7, slow * 3 / 4, slow, 2, 15), 7, "the hysteresis band holds");
    }

    #[test]
    fn bitmap_pace_degenerate_threshold_holds_the_rate() {
        // negative control: slow_us = 0 (misconfigured) must not stall the stream to zero
        assert!(bitmap_pace(15, u64::MAX, 0, 2, 15) >= 2);
        assert_eq!(bitmap_pace(15, 0, 0, 2, 15), 15);
    }
}

pub fn check_avc420_ready(gfx: &Gfx) -> bool {
    gfx.ready.load(Ordering::SeqCst) && gfx.avc420.load(Ordering::SeqCst)
}

pub fn check_egfx_ready(gfx: &Gfx) -> bool {
    check_avc420_ready(gfx) || (gfx.ready.load(Ordering::SeqCst) && gfx.progressive.load(Ordering::SeqCst))
}
