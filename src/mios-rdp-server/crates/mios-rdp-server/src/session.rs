//! One RDP connection at a time into the operator's live session: the IronRDP display/connection
//! hooks, the accept loop (source allow-list before a byte is parsed, ConnectionPolicy Reject while a
//! session is live, TCP keepalive + TCP_USER_TIMEOUT so a dead phone is dropped in ~15 s), and the
//! per-session lifecycle (display service attach, capture, encode pipeline, input, stats).

use std::net::{IpAddr, SocketAddr};
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use anyhow::{Context as _, Result};
use ironrdp_displaycontrol::pdu::DisplayControlMonitorLayout;
use ironrdp_server::{
    BitmapUpdate, ConnectionHandler, ConnectionInfo, DesktopSize, DisplayContext, DisplayUpdate, PixelFormat,
    RdpServerDisplay, RdpServerDisplayUpdates, ServerResult,
};
use serde_json::json;
use tracing::{error, info, warn};

use crate::capture::{self, CaptureHandle, CaptureParams, Frame};
use crate::config::Config;
use crate::display::{Applied, DisplayClient};
use crate::input;
use crate::pipeline::{self, Gfx, PipeParams, PipeStats};

pub struct Session {
    pub cfg: Config,
    pub display: Arc<DisplayClient>,
    pub gfx: Arc<Gfx>,
    pub stats: Arc<PipeStats>,
    pub authenticated: Arc<AtomicBool>,
    pub conn_suppressed: Mutex<Option<Arc<AtomicBool>>>,
    pub suppressed: Arc<AtomicBool>,
    pub operator_paused: Arc<AtomicBool>,
    pub desktop: Arc<AtomicU64>,
    pub layout: Arc<AtomicU32>,
    pub capture_hz: Arc<AtomicU32>,
    pub refresh: Arc<AtomicBool>,
    pub input_tx: Mutex<std::sync::mpsc::Sender<input::Cmd>>,
    pub input_counters: Arc<input::InputCounters>,
    pub clip_counters: Arc<crate::clip::ClipCounters>,
    pub capture: Mutex<Option<CaptureHandle>>,
    pub capture_info: Mutex<Option<Arc<Mutex<capture::CaptureInfo>>>>,
    pub capture_stats: Mutex<Option<Arc<capture::CaptureStats>>>,
    pub pipe: Mutex<Option<(Arc<AtomicBool>, std::thread::JoinHandle<()>)>>,
    pub bitmap: Arc<pipeline::BitmapSlot>,
    pub mailbox: Mutex<Option<Arc<capture::Mailbox>>>,
    pub applied: Mutex<Option<Applied>>,
    pub attached: AtomicBool,
    pub peer: crate::auth::CurrentPeer,
    pub client: Mutex<serde_json::Value>,
    pub connected_at: Mutex<Option<Instant>>,
    pub out_hz: AtomicU32,
    pub user: Arc<Mutex<Option<String>>>,
    pub cookie_handle: Mutex<Option<ironrdp_server::AutoReconnectCookieHandle>>,
    pub disconnect_handle: Mutex<Option<ironrdp_server::ErrorInfoDisconnectHandle>>,
    pub next_cookie: Mutex<Option<ironrdp_server::ServerAutoReconnect>>,
    pub arc_rejected: AtomicBool,
}

impl Session {
    pub fn desktop_size(&self) -> (u32, u32) {
        let v = self.desktop.load(Ordering::Relaxed);
        ((v >> 32) as u32, (v & 0xffff_ffff) as u32)
    }

    fn set_desktop(&self, w: u32, h: u32) {
        self.desktop.store((u64::from(w) << 32) | u64::from(h), Ordering::SeqCst);
    }

    /// New TCP connection (before any byte): fresh per-connection state, nothing authenticated.
    pub fn begin_connection(&self, peer: SocketAddr) {
        self.gfx.reset();
        self.authenticated.store(false, Ordering::SeqCst);
        self.arc_rejected.store(false, Ordering::SeqCst);
        *self.next_cookie.lock().expect("cookie") = None;
        *self.conn_suppressed.lock().expect("sup") = None;
        self.suppressed.store(false, Ordering::SeqCst);
        self.operator_paused.store(false, Ordering::SeqCst);
        *self.peer.lock().expect("peer") = Some(peer.ip());
        *self.connected_at.lock().expect("t") = Some(Instant::now());
        *self.client.lock().expect("client") = json!({"peer": peer.to_string()});
        self.stats.bitmap_write.lock().expect("bitmap write").clear();
        drop(self.bitmap.take_pending()); // never hand a previous connection's pending frame to a new one
    }

    /// Post-authentication: remote output, capture and encode pipeline.
    fn start(&self, w: u32, h: u32) -> Result<Applied> {
        let applied = self.display.attach(w, h).context("display service attach")?;
        info!(output = %applied.output, w = applied.width, h = applied.height, scale = applied.scale, x = applied.x, y = applied.y, "remote output attached");
        self.set_desktop(applied.width, applied.height);
        self.out_hz.store(applied.hz, Ordering::Relaxed);
        *self.applied.lock().expect("applied") = Some(applied.clone());
        self.layout.store(0, Ordering::SeqCst);
        self.capture_hz.store(self.cfg.target_hz, Ordering::SeqCst);
        let stop = Arc::new(AtomicBool::new(false));
        let cap = capture::spawn(CaptureParams {
            output: applied.output.clone(),
            paint_cursor: self.cfg.native.cursor != "client",
            max_hz: Arc::clone(&self.capture_hz),
            layout: Arc::clone(&self.layout),
            paused: Arc::clone(&self.suppressed),
            stop: Arc::clone(&stop),
            matrix: crate::gpu::Matrix::parse(&self.cfg.native.color_matrix).expect("validated"),
            modifiers: self.cfg.native.capture_modifiers.clone(),
            path: self.cfg.native.capture_path.clone(),
            convert_threads: self.cfg.native.convert_threads,
            gpu_priority: self.cfg.native.gpu_priority.clone(),
            output_wait: Duration::from_secs(10),
        })?;
        *self.mailbox.lock().expect("mb") = Some(Arc::clone(&cap.mailbox));
        *self.capture_info.lock().expect("ci") = Some(Arc::clone(&cap.info));
        *self.capture_stats.lock().expect("cs") = Some(Arc::clone(&cap.stats));
        let pstop = Arc::new(AtomicBool::new(false));
        let jh = pipeline::spawn(
            PipeParams {
                native: self.cfg.native.clone(),
                target_hz: self.cfg.target_hz,
                bitmap: Arc::clone(&self.bitmap),
                layout: Arc::clone(&self.layout),
                capture_hz: Arc::clone(&self.capture_hz),
                authenticated: Arc::clone(&self.authenticated),
                suppressed: Arc::clone(&self.suppressed),
                stop: Arc::clone(&pstop),
                refresh_request: Arc::clone(&self.refresh),
                egfx_wait: Duration::from_millis(self.cfg.native.egfx_wait_ms),
            },
            Arc::clone(&self.gfx),
            Arc::clone(&cap.mailbox),
            Arc::clone(&self.stats),
        );
        *self.capture.lock().expect("cap") = Some(cap);
        *self.pipe.lock().expect("pipe") = Some((pstop, jh));
        self.attached.store(true, Ordering::SeqCst);
        Ok(applied)
    }

    /// Connection over: stop capture/encode, release input, hand the cursor back, linger the output.
    pub fn end_connection(&self, result: &ServerResult<()>) {
        self.stats.bitmap_write.lock().expect("bitmap write").clear();
        let dur = self.connected_at.lock().expect("t").take().map(|t| t.elapsed()).unwrap_or_default();
        let peer = self.peer.lock().expect("peer").map(|p| p.to_string()).unwrap_or_default();
        let was = self.attached.swap(false, Ordering::SeqCst);
        if let Some((stop, jh)) = self.pipe.lock().expect("pipe").take() {
            stop.store(true, Ordering::SeqCst);
            self.gfx.notify();
            if let Some(mb) = self.mailbox.lock().expect("mb").as_ref() {
                mb.notify();
            }
            let _ = jh.join();
        }
        if let Some(mut c) = self.capture.lock().expect("cap").take() {
            c.stop();
        }
        self.mailbox.lock().expect("mb").take();
        let _ = self.input_tx.lock().expect("input").send(input::Cmd::Release);
        self.authenticated.store(false, Ordering::SeqCst);
        if was {
            if let Err(e) = self.display.detach() {
                warn!("display detach: {e:#}");
            }
        }
        match result {
            Ok(()) => info!(%peer, secs = dur.as_secs(), attached = was, "client disconnected"),
            Err(e) => info!(%peer, secs = dur.as_secs(), attached = was, "connection ended: {e:#}"),
        }
        self.gfx.reset();
    }
}

/// RdpServerDisplay over the session (one per server, reused across connections).
pub struct Display {
    pub s: Arc<Session>,
}

#[async_trait::async_trait]
impl RdpServerDisplay for Display {
    async fn size(&mut self) -> DesktopSize {
        let (w, h) = self.s.desktop_size();
        DesktopSize { width: w.max(200) as u16, height: h.max(200) as u16 }
    }

    async fn request_initial_size(&mut self, client: DesktopSize) -> DesktopSize {
        // Called after the credential validator accepted (or a valid auto-reconnect cookie).
        if self.s.arc_rejected.load(Ordering::SeqCst) {
            return self.size().await;
        }
        if self.s.attached.load(Ordering::SeqCst) {
            return self.size().await; // deactivation-reactivation: keep the attached output
        }
        self.s.authenticated.store(true, Ordering::SeqCst);
        let max_w = u32::from(self.s.cfg.native.max_width).max(200);
        let max_h = u32::from(self.s.cfg.native.max_height).max(200);
        let w = u32::from(client.width).clamp(200, max_w) & !1;
        let h = u32::from(client.height).clamp(200, max_h) & !1;
        info!(client_w = client.width, client_h = client.height, w, h, "client desktop size");
        let s = Arc::clone(&self.s);
        let r = tokio::task::spawn_blocking(move || s.start(w, h)).await;
        match r {
            Ok(Ok(a)) => DesktopSize { width: a.width as u16, height: a.height as u16 },
            Ok(Err(e)) => {
                error!("could not start the remote display: {e:#}");
                self.s.set_desktop(w, h);
                DesktopSize { width: w as u16, height: h as u16 }
            }
            Err(e) => {
                error!("remote display start panicked: {e}");
                DesktopSize { width: w as u16, height: h as u16 }
            }
        }
    }

    async fn updates(&mut self, ctx: DisplayContext) -> ServerResult<Box<dyn RdpServerDisplayUpdates>> {
        self.s.suppressed.store(self.s.operator_paused.load(Ordering::Relaxed) || ctx.display_suppressed.load(Ordering::Relaxed), Ordering::Relaxed);
        *self.s.conn_suppressed.lock().expect("sup") = Some(ctx.display_suppressed);
        Ok(Box::new(Updates {
            bitmap: Arc::clone(&self.s.bitmap),
            hide_pointer: self.s.cfg.native.cursor != "client",
            size: self.s.desktop_size(),
            pending: None,
            reference: None,
            s: Arc::clone(&self.s),
        }))
    }

    fn request_layout(&mut self, layout: DisplayControlMonitorLayout) {
        let Some(m) = layout.monitors().iter().find(|m| m.is_primary()).or_else(|| layout.monitors().first()) else {
            return;
        };
        let (w, h) = m.dimensions();
        let dpi = m.physical_dimensions().and_then(|(pw, _)| (pw > 0).then(|| f64::from(w) * 25.4 / f64::from(pw)));
        info!(
            w,
            h,
            orientation = ?m.orientation(),
            physical = ?m.physical_dimensions(),
            desktop_scale = ?m.desktop_scale_factor(),
            device_scale = ?m.device_scale_factor().map(|d| d.value()),
            monitors = layout.monitors().len(),
            "DISP layout from the client"
        );
        if self.s.cfg.mode == "mirror" {
            info!("mode = mirror: client resize refused");
            return;
        }
        let max_w = u32::from(self.s.cfg.native.max_width).max(200);
        let max_h = u32::from(self.s.cfg.native.max_height).max(200);
        let (w, h) = (w.clamp(200, max_w) & !1, h.clamp(200, max_h) & !1);
        let s = Arc::clone(&self.s);
        std::thread::spawn(move || match s.display.layout(w, h, dpi) {
            Ok(a) => {
                info!(w = a.width, h = a.height, scale = a.scale, "remote output resized");
                s.set_desktop(a.width, a.height);
                *s.applied.lock().expect("applied") = Some(a);
            }
            Err(e) => warn!("resize refused: {e:#}"),
        });
    }
}

struct Updates {
    bitmap: Arc<pipeline::BitmapSlot>,
    hide_pointer: bool,
    size: (u32, u32),
    pending: Option<Frame>,
    reference: Option<(u32,u32,u32,bytes::Bytes)>,
    s: Arc<Session>,
}

#[async_trait::async_trait]
impl RdpServerDisplayUpdates for Updates {
    async fn next_update(&mut self) -> ServerResult<Option<DisplayUpdate>> {
        // IronRDP's serial display loop has completed the preceding bitmap's
        // encoder and writes before entering here. This is socket completion,
        // not a client-render acknowledgement (the bitmap protocol has none).
        let completed = self.s.stats.bitmap_write.lock().expect("bitmap write").complete(Instant::now());
        if let Some((service,total)) = completed {
            self.s.stats.bitmap_delivered.fetch_add(1,Ordering::Relaxed);
            let mut samples=self.s.stats.samples.lock().expect("samples");
            samples.bitmap_write_us.push(service);samples.bitmap_capture_to_write_us.push(total);
        }
        if self.s.arc_rejected.load(Ordering::SeqCst) {
            return Ok(None);
        }
        if self.hide_pointer {
            // the pointer is composited into the frames (scaled Hyprland outputs cannot export it)
            self.hide_pointer = false;
            return Ok(Some(DisplayUpdate::HidePointer));
        }
        loop {
        let mut f = match self.pending.take() {
            Some(f) => f,
            None => {
                let f = self.bitmap.take().await;
                let taken = Instant::now();
                let st = &self.s.stats;
                let mut smp = st.samples.lock().expect("samples");
                smp.convert_us.push(f.convert_us);
                smp.capture_to_send_us.push(taken.duration_since(f.ready_at).as_micros() as u64);
                drop(smp);
                f
            }
        };
        if (f.width, f.height) != self.size {
            self.size = (f.width, f.height);
            self.pending = Some(f);
            return Ok(Some(DisplayUpdate::Resize(DesktopSize { width: self.size.0 as u16, height: self.size.1 as u16 })));
        }
        if let Some((w,h,stride,reference))=&self.reference {
            if (*w,*h,*stride)==(f.width,f.height,f.stride) &&
                !pipeline::stabilize_bitmap(&mut f.data,reference,f.width as usize,f.height as usize,f.stride as usize,self.s.cfg.native.bitmap_damage_threshold) {
                self.s.stats.skipped_static.fetch_add(1,Ordering::Relaxed);
                continue;
            }
        }
        let (w, h, stride) = (f.width, f.height, f.stride);
        let ready_at=f.ready_at;
        let data=bytes::Bytes::from(f.data);
        self.reference=Some((w,h,stride,data.clone()));
        let upd = BitmapUpdate {
            x: 0,
            y: 0,
            width: std::num::NonZeroU16::new(w as u16).expect("w"),
            height: std::num::NonZeroU16::new(h as u16).expect("h"),
            format: PixelFormat::BgrA32,
            data,
            stride: std::num::NonZeroUsize::new(stride as usize).expect("stride"),
        };
        let _ = &self.s;
        self.s.stats.bitmap_write.lock().expect("bitmap write").begin(ready_at,Instant::now());
        return Ok(Some(DisplayUpdate::Bitmap(upd)));
        }
    }
}

pub struct Hooks {
    pub s: Arc<Session>,
}

impl ConnectionHandler for Hooks {
    fn on_connection_info(&mut self, info: &ConnectionInfo) {
        let user = self.s.user.lock().expect("user").clone();
        if info.auto_reconnect && self.s.cfg.auth == "priv" {
            let authorized = user.as_deref().is_some_and(|u| crate::auth::check_user_authorized(u, "mios-rdp"));
            if !authorized {
                warn!(user = ?user, "Auto-reconnect user authorization rejected");
                self.s.arc_rejected.store(true, Ordering::SeqCst);
                self.s.authenticated.store(false, Ordering::SeqCst);
                let inv = crate::random_cookie();
                *self.s.next_cookie.lock().expect("cookie") = Some(inv.clone());
                if let Some(ref ch) = *self.s.cookie_handle.lock().expect("ch") {
                    let _ = ch.set(Some(inv));
                }
                if let Some(ref dh) = *self.s.disconnect_handle.lock().expect("dh") {
                    use ironrdp_pdu::rdp::server_error_info::{ErrorInfo, ProtocolIndependentCode};
                    let _ = dh.disconnect(ErrorInfo::ProtocolIndependentCode(ProtocolIndependentCode::ServerDeniedConnection));
                }
                return;
            }
        }
        if self.s.cfg.native.auto_reconnect {
            let fresh = crate::random_cookie();
            *self.s.next_cookie.lock().expect("cookie") = Some(fresh.clone());
            if let Some(ref ch) = *self.s.cookie_handle.lock().expect("ch") {
                let _ = ch.set(Some(fresh));
            }
        }
        self.s.authenticated.store(true, Ordering::SeqCst);
        info!(
            keyboard_layout = format!("{:#x}", info.keyboard_layout),
            keyboard_type = ?info.keyboard_type,
            auto_reconnect = info.auto_reconnect,
            user = ?user,
            "session established"
        );
        let mut c = self.s.client.lock().expect("client");
        c["keyboard_layout"] = json!(format!("{:#x}", info.keyboard_layout));
        c["auto_reconnect"] = json!(info.auto_reconnect);
        c["user"] = json!(user);
    }
}

/// TCP keepalive 5 s idle / 2 s interval / 3 probes + TCP_USER_TIMEOUT 10 s.
pub fn tune_socket(s: &tokio::net::TcpStream, idle: u64) {
    use std::os::fd::AsRawFd as _;
    let fd = s.as_raw_fd();
    let set = |level: i32, opt: i32, v: i32| {
        // SAFETY: integer socket option on a live TCP socket.
        #[allow(unsafe_code)]
        unsafe {
            libc::setsockopt(fd, level, opt, (&raw const v).cast(), size_of::<i32>() as u32);
        }
    };
    set(libc::SOL_SOCKET, libc::SO_KEEPALIVE, 1);
    set(libc::IPPROTO_TCP, libc::TCP_KEEPIDLE, idle.max(1) as i32);
    set(libc::IPPROTO_TCP, libc::TCP_KEEPINTVL, 2);
    set(libc::IPPROTO_TCP, libc::TCP_KEEPCNT, 3);
    set(libc::IPPROTO_TCP, libc::TCP_USER_TIMEOUT, 10_000);
    let _ = s.set_nodelay(true);
}

pub fn peer_ok(cfg: &Config, ip: IpAddr) -> bool {
    cfg.peer_allowed(ip)
}
