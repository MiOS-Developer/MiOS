//! Clipboard (MS-RDPECLIP cliprdr) <-> Wayland selection through ext-data-control-v1 (no focused
//! surface needed). Text (CF_UNICODETEXT <-> text/plain;charset=utf-8), HTML ("HTML Format" <->
//! text/html) and images (CF_DIB / registered "PNG" <-> image/png). Lazy both ways: data moves only
//! when something pastes. Our own selections carry a marker MIME type and are never echoed back;
//! offers marked x-kde-passwordManagerHint are never sent to the client; direction and size are
//! SSOT keys ([remote.rdp.redirect].clipboard, clipboard_max_mb).
#![allow(unsafe_code)]

use std::collections::{HashMap, VecDeque};
use std::fs::File;
use std::io::{Read as _, Write as _};
use std::os::fd::{AsFd, AsRawFd, FromRawFd, OwnedFd};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::mpsc::{Receiver, Sender};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use anyhow::{Context as _, Result, anyhow, bail};
use ironrdp_cliprdr::backend::{ClipboardMessage, CliprdrBackend, CliprdrBackendFactory};
use ironrdp_cliprdr::pdu::{
    ClipboardFormat, ClipboardFormatId, ClipboardFormatName, ClipboardGeneralCapabilityFlags, FileContentsRequest,
    FileContentsResponse, FormatDataRequest, FormatDataResponse, LockDataId, OwnedFormatDataResponse,
};
use ironrdp_core::impl_as_any;
use ironrdp_server::{CliprdrServerFactory, ServerEvent, ServerEventSender};
use tokio::sync::mpsc::UnboundedSender;
use tracing::{debug, info, warn};
use wayland_client::globals::{GlobalListContents, registry_queue_init};
use wayland_client::protocol::{wl_registry, wl_seat};
use wayland_client::{Connection, Dispatch, Proxy, QueueHandle, backend::ObjectId, delegate_noop, event_created_child};
use wayland_protocols::ext::data_control::v1::client::{
    ext_data_control_device_v1::{self, ExtDataControlDeviceV1},
    ext_data_control_manager_v1::ExtDataControlManagerV1,
    ext_data_control_offer_v1::{self, ExtDataControlOfferV1},
    ext_data_control_source_v1::{self, ExtDataControlSourceV1},
};

pub const MARKER: &str = "application/x-mios-rdp-clipboard";
const SECRET_HINT: &str = "x-kde-passwordManagerHint";
const FMT_HTML: u32 = 0xC0F0;
const FMT_PNG: u32 = 0xC0F1;
const TEXT_MIMES: [&str; 5] = ["text/plain;charset=utf-8", "UTF8_STRING", "text/plain", "TEXT", "STRING"];

#[derive(Debug)]
pub enum FromRdp {
    Ready,
    RemoteCopy(Vec<ClipboardFormat>),
    DataRequest(ClipboardFormatId),
    DataResponse(OwnedFormatDataResponse),
    Closed,
}

#[derive(Default)]
pub struct ClipCounters {
    pub to_client: AtomicU64,
    pub to_host: AtomicU64,
    pub refused_secret: AtomicU64,
    pub refused_size: AtomicU64,
    pub refused_direction: AtomicU64,
}

pub struct Shared {
    pub tx: Mutex<Sender<FromRdp>>,
    pub wake: OwnedFd,
    pub events: Mutex<Option<UnboundedSender<ServerEvent>>>,
}

impl Shared {
    fn send(&self, m: FromRdp) {
        let _ = self.tx.lock().expect("clip").send(m);
        let b = 1u8;
        // SAFETY: one byte into our own non-blocking pipe.
        unsafe {
            libc::write(self.wake.as_raw_fd(), (&raw const b).cast(), 1);
        }
    }
}

#[derive(Debug)]
pub struct Backend {
    shared: Arc<SharedDbg>,
}

pub struct SharedDbg(pub Arc<Shared>);
impl std::fmt::Debug for SharedDbg {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("clip::Shared")
    }
}

impl_as_any!(Backend);

impl CliprdrBackend for Backend {
    fn temporary_directory(&self) -> &str {
        ""
    }
    fn client_capabilities(&self) -> ClipboardGeneralCapabilityFlags {
        ClipboardGeneralCapabilityFlags::USE_LONG_FORMAT_NAMES
    }
    fn on_ready(&mut self) {
        self.shared.0.send(FromRdp::Ready);
    }
    fn on_request_format_list(&mut self) {
        self.shared.0.send(FromRdp::Ready);
    }
    fn on_process_negotiated_capabilities(&mut self, capabilities: ClipboardGeneralCapabilityFlags) {
        debug!(?capabilities, "cliprdr capabilities");
    }
    fn on_remote_copy(&mut self, available_formats: &[ClipboardFormat]) {
        self.shared.0.send(FromRdp::RemoteCopy(available_formats.to_vec()));
    }
    fn on_format_data_request(&mut self, request: FormatDataRequest) {
        self.shared.0.send(FromRdp::DataRequest(request.format));
    }
    fn on_format_data_response(&mut self, response: FormatDataResponse<'_>) {
        use ironrdp_core::IntoOwned as _;
        self.shared.0.send(FromRdp::DataResponse(response.into_owned()));
    }
    fn on_file_contents_request(&mut self, _request: FileContentsRequest) {}
    fn on_file_contents_response(&mut self, _response: FileContentsResponse<'_>) {}
    fn on_lock(&mut self, _data_id: LockDataId) {}
    fn on_unlock(&mut self, _data_id: LockDataId) {}
}

impl Drop for Backend {
    fn drop(&mut self) {
        self.shared.0.send(FromRdp::Closed);
    }
}

pub struct Factory {
    pub shared: Arc<Shared>,
}

impl CliprdrBackendFactory for Factory {
    fn build_cliprdr_backend(&self) -> Box<dyn CliprdrBackend> {
        Box::new(Backend { shared: Arc::new(SharedDbg(Arc::clone(&self.shared))) })
    }
}

impl ServerEventSender for Factory {
    fn set_sender(&mut self, sender: UnboundedSender<ServerEvent>) {
        *self.shared.events.lock().expect("clip") = Some(sender);
    }
}

impl CliprdrServerFactory for Factory {}

pub struct Params {
    /// "both" | "to-client" | "to-host" | "off"
    pub direction: String,
    pub max_bytes: usize,
    pub authenticated: Arc<AtomicBool>,
    pub counters: Arc<ClipCounters>,
}

/// Create the shared state and start the clipboard thread.
pub fn spawn(p: Params) -> Result<Arc<Shared>> {
    let (tx, rx) = std::sync::mpsc::channel();
    let mut fds = [0i32; 2];
    // SAFETY: pipe2 into a local array.
    if unsafe { libc::pipe2(fds.as_mut_ptr(), libc::O_CLOEXEC | libc::O_NONBLOCK) } != 0 {
        bail!("pipe2: {}", std::io::Error::last_os_error());
    }
    // SAFETY: fresh descriptors owned here.
    let (rd, wr) = unsafe { (OwnedFd::from_raw_fd(fds[0]), OwnedFd::from_raw_fd(fds[1])) };
    let shared = Arc::new(Shared { tx: Mutex::new(tx), wake: wr, events: Mutex::new(None) });
    let s2 = Arc::clone(&shared);
    std::thread::Builder::new()
        .name("mios-rdp-clipboard".into())
        .spawn(move || {
            if let Err(e) = run(p, rx, rd, s2) {
                warn!("clipboard thread ended: {e:#}");
            }
        })
        .context("spawn clipboard thread")?;
    Ok(shared)
}

struct Offer {
    mimes: Vec<String>,
}

struct WlState {
    offers: HashMap<ObjectId, Offer>,
    selection: Option<ExtDataControlOfferV1>,
    selection_changed: bool,
    sends: Vec<(String, OwnedFd)>,
    source_cancelled: bool,
}

impl Dispatch<wl_registry::WlRegistry, GlobalListContents> for WlState {
    fn event(_: &mut Self, _: &wl_registry::WlRegistry, _: wl_registry::Event, _: &GlobalListContents, _: &Connection, _: &QueueHandle<Self>) {}
}
delegate_noop!(WlState: ignore wl_seat::WlSeat);
delegate_noop!(WlState: ignore ExtDataControlManagerV1);

impl Dispatch<ExtDataControlDeviceV1, ()> for WlState {
    fn event(s: &mut Self, _: &ExtDataControlDeviceV1, e: ext_data_control_device_v1::Event, _: &(), _: &Connection, _: &QueueHandle<Self>) {
        match e {
            ext_data_control_device_v1::Event::DataOffer { id } => {
                s.offers.insert(id.id(), Offer { mimes: Vec::new() });
            }
            ext_data_control_device_v1::Event::Selection { id } => {
                if let Some(old) = s.selection.take() {
                    s.offers.remove(&old.id());
                    old.destroy();
                }
                s.selection = id;
                s.selection_changed = true;
            }
            ext_data_control_device_v1::Event::PrimarySelection { id } => {
                if let Some(o) = id {
                    s.offers.remove(&o.id());
                    o.destroy();
                }
            }
            _ => {}
        }
    }
    event_created_child!(WlState, ExtDataControlDeviceV1, [
        ext_data_control_device_v1::EVT_DATA_OFFER_OPCODE => (ExtDataControlOfferV1, ()),
    ]);
}

impl Dispatch<ExtDataControlOfferV1, ()> for WlState {
    fn event(s: &mut Self, o: &ExtDataControlOfferV1, e: ext_data_control_offer_v1::Event, _: &(), _: &Connection, _: &QueueHandle<Self>) {
        if let ext_data_control_offer_v1::Event::Offer { mime_type } = e {
            if let Some(of) = s.offers.get_mut(&o.id()) {
                of.mimes.push(mime_type);
            }
        }
    }
}

impl Dispatch<ExtDataControlSourceV1, ()> for WlState {
    fn event(s: &mut Self, _: &ExtDataControlSourceV1, e: ext_data_control_source_v1::Event, _: &(), _: &Connection, _: &QueueHandle<Self>) {
        match e {
            ext_data_control_source_v1::Event::Send { mime_type, fd } => s.sends.push((mime_type, fd)),
            ext_data_control_source_v1::Event::Cancelled => s.source_cancelled = true,
            _ => {}
        }
    }
}

fn is_text(m: &str) -> bool {
    TEXT_MIMES.contains(&m)
}

fn rdp_formats_for(mimes: &[String]) -> Vec<ClipboardFormat> {
    let mut f = Vec::new();
    if mimes.iter().any(|m| is_text(m)) {
        f.push(ClipboardFormat::new(ClipboardFormatId::CF_UNICODETEXT));
    }
    if mimes.iter().any(|m| m == "text/html") {
        f.push(ClipboardFormat::new(ClipboardFormatId::new(FMT_HTML)).with_name(ClipboardFormatName::HTML));
    }
    if mimes.iter().any(|m| m == "image/png") {
        f.push(ClipboardFormat::new(ClipboardFormatId::CF_DIB));
        f.push(ClipboardFormat::new(ClipboardFormatId::new(FMT_PNG)).with_name(ClipboardFormatName::new_static("PNG")));
    }
    f
}

/// What we offer on the host for the client's format list: (mime, client format id).
fn host_mimes_for(formats: &[ClipboardFormat]) -> Vec<(String, u32)> {
    let mut out = Vec::new();
    let named = |n: &str| formats.iter().find(|f| f.name.as_ref().is_some_and(|x| x.value() == n)).map(|f| f.id.value());
    if let Some(t) = formats.iter().find(|f| f.id == ClipboardFormatId::CF_UNICODETEXT) {
        for m in TEXT_MIMES {
            out.push((m.to_owned(), t.id.value()));
        }
    }
    if let Some(id) = named("HTML Format") {
        out.push(("text/html".to_owned(), id));
    }
    if let Some(id) = named("PNG") {
        out.push(("image/png".to_owned(), id));
    } else if let Some(f) = formats.iter().find(|f| f.id == ClipboardFormatId::CF_DIB || f.id == ClipboardFormatId::CF_DIBV5) {
        out.push(("image/png".to_owned(), f.id.value()));
    }
    out
}

pub fn utf16le_to_string(b: &[u8]) -> String {
    let u: Vec<u16> = b.chunks_exact(2).map(|c| u16::from_le_bytes([c[0], c[1]])).take_while(|c| *c != 0).collect();
    String::from_utf16_lossy(&u)
}

pub fn string_to_utf16le(s: &str) -> Vec<u8> {
    let mut v: Vec<u8> = s.encode_utf16().flat_map(u16::to_le_bytes).collect();
    v.extend_from_slice(&[0, 0]);
    v
}

/// Windows CF_HTML ("HTML Format") around an HTML fragment.
pub fn html_to_cf_html(html: &str) -> Vec<u8> {
    let pre = "<html><body>\r\n<!--StartFragment-->";
    let post = "<!--EndFragment-->\r\n</body></html>";
    let header_len = "Version:0.9\r\nStartHTML:0000000000\r\nEndHTML:0000000000\r\nStartFragment:0000000000\r\nEndFragment:0000000000\r\n".len();
    let start_html = header_len;
    let start_frag = start_html + pre.len();
    let end_frag = start_frag + html.len();
    let end_html = end_frag + post.len();
    let mut s = format!(
        "Version:0.9\r\nStartHTML:{start_html:010}\r\nEndHTML:{end_html:010}\r\nStartFragment:{start_frag:010}\r\nEndFragment:{end_frag:010}\r\n"
    );
    s.push_str(pre);
    s.push_str(html);
    s.push_str(post);
    let mut b = s.into_bytes();
    b.push(0);
    b
}

pub fn cf_html_to_html(b: &[u8]) -> String {
    let s = String::from_utf8_lossy(b);
    let num = |key: &str| -> Option<usize> {
        let i = s.find(key)? + key.len();
        s[i..].split(['\r', '\n']).next()?.trim().parse().ok()
    };
    match (num("StartFragment:"), num("EndFragment:")) {
        (Some(a), Some(e)) if a <= e && e <= b.len() => String::from_utf8_lossy(&b[a..e]).into_owned(),
        _ => s.trim_end_matches('\0').to_owned(),
    }
}

/// PNG -> CF_DIB (BITMAPINFOHEADER, 32 bpp BI_RGB, bottom-up BGRA rows).
pub fn png_to_dib(png_bytes: &[u8]) -> Result<Vec<u8>> {
    let mut dec = png::Decoder::new(std::io::Cursor::new(png_bytes));
    dec.set_transformations(png::Transformations::EXPAND | png::Transformations::STRIP_16 | png::Transformations::ALPHA);
    let mut r = dec.read_info().context("PNG header")?;
    let mut buf = vec![0; r.output_buffer_size().ok_or_else(|| anyhow!("PNG too large"))?];
    let info = r.next_frame(&mut buf).context("PNG data")?;
    let (w, h) = (info.width as usize, info.height as usize);
    let ch = match info.color_type {
        png::ColorType::Rgba => 4,
        png::ColorType::GrayscaleAlpha => 2,
        png::ColorType::Rgb => 3,
        png::ColorType::Grayscale => 1,
        png::ColorType::Indexed => bail!("indexed PNG after EXPAND"),
    };
    let mut out = Vec::with_capacity(40 + w * h * 4);
    for v in [40u32, w as u32, h as u32] {
        out.extend_from_slice(&v.to_le_bytes());
    }
    out.extend_from_slice(&1u16.to_le_bytes());
    out.extend_from_slice(&32u16.to_le_bytes());
    for v in [0u32, (w * h * 4) as u32, 2835, 2835, 0, 0] {
        out.extend_from_slice(&v.to_le_bytes());
    }
    for y in (0..h).rev() {
        let row = &buf[y * info.line_size..y * info.line_size + w * ch];
        for x in 0..w {
            let p = &row[x * ch..x * ch + ch];
            let (r, g, b, a) = match ch {
                4 => (p[0], p[1], p[2], p[3]),
                3 => (p[0], p[1], p[2], 255),
                2 => (p[0], p[0], p[0], p[1]),
                _ => (p[0], p[0], p[0], 255),
            };
            out.extend_from_slice(&[b, g, r, a]);
        }
    }
    Ok(out)
}

/// CF_DIB / CF_DIBV5 (24 or 32 bpp, BI_RGB or BI_BITFIELDS) -> PNG.
pub fn dib_to_png(d: &[u8]) -> Result<Vec<u8>> {
    let u32at = |o: usize| -> Result<u32> { Ok(u32::from_le_bytes(d.get(o..o + 4).ok_or_else(|| anyhow!("short DIB"))?.try_into()?)) };
    let size = u32at(0)? as usize;
    if !(40..=124).contains(&size) || d.len() < size {
        bail!("unsupported DIB header size {size}");
    }
    let w = u32at(4)? as i32;
    let h = u32at(8)? as i32;
    let bpp = u16::from_le_bytes([d[14], d[15]]);
    let comp = u32at(16)?;
    let colors = u32at(32)? as usize;
    if w <= 0 || h == 0 || w > 16384 || h.unsigned_abs() > 16384 {
        bail!("DIB size {w}x{h}");
    }
    if !(bpp == 24 || bpp == 32) || !(comp == 0 || comp == 3) {
        bail!("DIB {bpp} bpp compression {comp} not supported");
    }
    let mut off = size;
    if comp == 3 && size == 40 {
        off += 12; // three DWORD masks follow a BITMAPINFOHEADER
    }
    off += colors * 4;
    let (w, top_down, hh) = (w as usize, h < 0, h.unsigned_abs() as usize);
    let stride = (w * bpp as usize).div_ceil(32) * 4;
    if d.len() < off + stride * hh {
        bail!("DIB pixel data truncated");
    }
    let mut rgba = vec![0u8; w * hh * 4];
    let mut any_alpha = false;
    for y in 0..hh {
        let src_row = if top_down { y } else { hh - 1 - y };
        let row = &d[off + src_row * stride..off + src_row * stride + stride];
        for x in 0..w {
            let px = &row[x * (bpp as usize / 8)..];
            let a = if bpp == 32 { px[3] } else { 255 };
            any_alpha |= bpp == 32 && a != 0;
            rgba[(y * w + x) * 4..(y * w + x) * 4 + 4].copy_from_slice(&[px[2], px[1], px[0], a]);
        }
    }
    if bpp == 32 && !any_alpha {
        rgba.chunks_exact_mut(4).for_each(|p| p[3] = 255); // BI_RGB 32 bpp: alpha unused
    }
    let mut out = Vec::new();
    {
        let mut enc = png::Encoder::new(&mut out, w as u32, hh as u32);
        enc.set_color(png::ColorType::Rgba);
        enc.set_depth(png::BitDepth::Eight);
        let mut wr = enc.write_header()?;
        wr.write_image_data(&rgba)?;
    }
    Ok(out)
}

fn read_offer(conn: &Connection, offer: &ExtDataControlOfferV1, mime: &str, max: usize) -> Result<Vec<u8>> {
    let mut fds = [0i32; 2];
    // SAFETY: pipe2 into a local array.
    if unsafe { libc::pipe2(fds.as_mut_ptr(), libc::O_CLOEXEC) } != 0 {
        bail!("pipe2");
    }
    // SAFETY: fresh descriptors owned here.
    let (rd, wr) = unsafe { (OwnedFd::from_raw_fd(fds[0]), OwnedFd::from_raw_fd(fds[1])) };
    offer.receive(mime.to_owned(), wr.as_fd());
    conn.flush().context("flush receive")?;
    drop(wr);
    let mut f = File::from(rd);
    let mut out = Vec::new();
    let mut chunk = [0u8; 65536];
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        let mut pfd = libc::pollfd { fd: f.as_raw_fd(), events: libc::POLLIN, revents: 0 };
        // SAFETY: one pollfd.
        let r = unsafe { libc::poll(&mut pfd, 1, 200) };
        if r == 0 {
            if Instant::now() > deadline {
                bail!("selection owner did not deliver {mime} within 5 s");
            }
            continue;
        }
        let n = f.read(&mut chunk)?;
        if n == 0 {
            break;
        }
        out.extend_from_slice(&chunk[..n]);
        if out.len() > max {
            bail!("selection larger than clipboard_max_mb");
        }
    }
    Ok(out)
}

fn run(p: Params, rx: Receiver<FromRdp>, wake: OwnedFd, shared: Arc<Shared>) -> Result<()> {
    let conn = Connection::connect_to_env().context("connect to the Wayland session")?;
    let (globals, mut queue) = registry_queue_init::<WlState>(&conn)?;
    let qh = queue.handle();
    let seat: wl_seat::WlSeat = globals.bind(&qh, 1..=9, ())?;
    let mgr: ExtDataControlManagerV1 = globals.bind(&qh, 1..=1, ()).context("ext_data_control_manager_v1")?;
    let device = mgr.get_data_device(&seat, &qh, ());
    let mut st = WlState { offers: HashMap::new(), selection: None, selection_changed: false, sends: Vec::new(), source_cancelled: false };
    queue.roundtrip(&mut st)?;
    st.selection_changed = false; // the selection that existed before the client: advertised on Ready
    let to_client = matches!(p.direction.as_str(), "both" | "to-client");
    let to_host = matches!(p.direction.as_str(), "both" | "to-host");
    info!(direction = %p.direction, max_mb = p.max_bytes / (1024 * 1024), "clipboard (ext-data-control) ready");

    let mut ready = false;
    let mut source: Option<(ExtDataControlSourceV1, Vec<(String, u32)>)> = None;
    let mut waiting: VecDeque<(u32, Vec<OwnedFd>)> = VecDeque::new(); // requested client formats, FIFO
    let mut advertised: Option<Vec<String>> = None;
    let send_ev = |m: ClipboardMessage| {
        if let Some(tx) = shared.events.lock().expect("clip").as_ref() {
            let _ = tx.send(ServerEvent::Clipboard(m));
        }
    };

    loop {
        match queue.flush() {
            Ok(()) => {}
            Err(wayland_client::backend::WaylandError::Io(e)) if e.kind() == std::io::ErrorKind::WouldBlock => {}
            Err(e) => return Err(e.into()),
        }
        if let Some(guard) = queue.prepare_read() {
            let mut pfds = [
                libc::pollfd { fd: guard.connection_fd().as_raw_fd(), events: libc::POLLIN, revents: 0 },
                libc::pollfd { fd: wake.as_raw_fd(), events: libc::POLLIN, revents: 0 },
            ];
            // SAFETY: two pollfds.
            unsafe { libc::poll(pfds.as_mut_ptr(), 2, 500) };
            if pfds[0].revents & libc::POLLIN != 0 {
                match guard.read() {
                    Ok(_) => {}
                    Err(wayland_client::backend::WaylandError::Io(e)) if e.kind() == std::io::ErrorKind::WouldBlock => {}
                    Err(e) => return Err(e.into()),
                }
            } else {
                drop(guard);
            }
            if pfds[1].revents & libc::POLLIN != 0 {
                let mut b = [0u8; 64];
                // SAFETY: drain our own pipe.
                unsafe { libc::read(wake.as_raw_fd(), b.as_mut_ptr().cast(), b.len()) };
            }
        }
        queue.dispatch_pending(&mut st)?;
        let authed = p.authenticated.load(Ordering::SeqCst);

        // host selection changed -> advertise to the client
        let advertise_now = st.selection_changed || (ready && advertised.is_none());
        if advertise_now && ready && authed {
            st.selection_changed = false;
            let mimes = st.selection.as_ref().and_then(|o| st.offers.get(&o.id())).map(|o| o.mimes.clone()).unwrap_or_default();
            if mimes.iter().any(|m| m == MARKER) {
                debug!("selection is ours; not echoed");
            } else if mimes.iter().any(|m| m == SECRET_HINT) {
                p.counters.refused_secret.fetch_add(1, Ordering::Relaxed);
                info!("selection marked secret ({SECRET_HINT}); not offered to the client");
                send_ev(ClipboardMessage::SendInitiateCopy(Vec::new()));
            } else if !to_client {
                p.counters.refused_direction.fetch_add(1, Ordering::Relaxed);
            } else if advertised.as_ref() != Some(&mimes) || st.selection.is_some() {
                let f = rdp_formats_for(&mimes);
                info!(mimes = ?mimes, formats = f.len(), "host selection offered to the client");
                send_ev(ClipboardMessage::SendInitiateCopy(f));
            }
            advertised = Some(mimes);
        }

        for (mime, fd) in std::mem::take(&mut st.sends) {
            // a host app pastes the client's clipboard: fetch the client format lazily
            let Some((_, map)) = source.as_ref() else { continue };
            let Some((_, fmt)) = map.iter().find(|(m, _)| *m == mime) else { continue };
            if let Some(w) = waiting.iter_mut().find(|(f, _)| f == fmt) {
                w.1.push(fd);
            } else {
                waiting.push_back((*fmt, vec![fd]));
                send_ev(ClipboardMessage::SendInitiatePaste(ClipboardFormatId::new(*fmt)));
            }
        }
        if st.source_cancelled {
            st.source_cancelled = false;
            if let Some((s, _)) = source.take() {
                s.destroy();
            }
        }

        loop {
            let msg = match rx.try_recv() {
                Ok(m) => m,
                Err(_) => break,
            };
            if !authed && !matches!(msg, FromRdp::Closed) {
                continue; // nothing is serviced before authentication
            }
            match msg {
                FromRdp::Ready => {
                    ready = true;
                    advertised = None;
                }
                FromRdp::Closed => {
                    ready = false;
                    advertised = None;
                    waiting.clear();
                    if let Some((s, _)) = source.take() {
                        // leave the host selection as it is, but stop serving it from the client
                        device.set_selection(None);
                        s.destroy();
                    }
                }
                FromRdp::RemoteCopy(formats) => {
                    info!(formats = ?formats.iter().map(|f| (f.id.value(), f.name.as_ref().map(|n| n.value().to_owned()))).collect::<Vec<_>>(), "client clipboard format list");
                    if !to_host {
                        p.counters.refused_direction.fetch_add(1, Ordering::Relaxed);
                        continue;
                    }
                    let map = host_mimes_for(&formats);
                    if map.is_empty() {
                        continue;
                    }
                    let s = mgr.create_data_source(&qh, ());
                    for (m, _) in &map {
                        s.offer(m.clone());
                    }
                    s.offer(MARKER.to_owned());
                    device.set_selection(Some(&s));
                    if let Some((old, _)) = source.replace((s, map)) {
                        old.destroy();
                    }
                    debug!(formats = formats.len(), "client clipboard offered on the host");
                }
                FromRdp::DataRequest(fmt) => {
                    info!(format = fmt.value(), "client requests host clipboard data");
                    let resp = (|| -> Result<Vec<u8>> {
                        if !to_client {
                            bail!("clipboard direction {} refuses host -> client", p.direction);
                        }
                        let offer = st.selection.as_ref().ok_or_else(|| anyhow!("no host selection"))?;
                        let mimes = st.offers.get(&offer.id()).map(|o| o.mimes.clone()).unwrap_or_default();
                        if mimes.iter().any(|m| m == SECRET_HINT) {
                            p.counters.refused_secret.fetch_add(1, Ordering::Relaxed);
                            bail!("secret selection");
                        }
                        let pick = |c: &[&str]| c.iter().find(|m| mimes.iter().any(|x| x == *m)).map(|m| m.to_string());
                        if fmt == ClipboardFormatId::CF_UNICODETEXT {
                            let m = pick(&TEXT_MIMES).ok_or_else(|| anyhow!("no text"))?;
                            let b = read_offer(&conn, offer, &m, p.max_bytes)?;
                            Ok(string_to_utf16le(&String::from_utf8_lossy(&b)))
                        } else if fmt.value() == FMT_HTML {
                            let b = read_offer(&conn, offer, "text/html", p.max_bytes)?;
                            Ok(html_to_cf_html(&String::from_utf8_lossy(&b)))
                        } else if fmt == ClipboardFormatId::CF_DIB {
                            png_to_dib(&read_offer(&conn, offer, "image/png", p.max_bytes)?)
                        } else if fmt.value() == FMT_PNG {
                            read_offer(&conn, offer, "image/png", p.max_bytes)
                        } else {
                            bail!("format {} not offered", fmt.value())
                        }
                    })();
                    match resp {
                        Ok(b) => {
                            p.counters.to_client.fetch_add(1, Ordering::Relaxed);
                            send_ev(ClipboardMessage::SendFormatData(OwnedFormatDataResponse::new_data(b)));
                        }
                        Err(e) => {
                            if format!("{e:#}").contains("clipboard_max_mb") {
                                p.counters.refused_size.fetch_add(1, Ordering::Relaxed);
                            }
                            info!("clipboard request refused: {e:#}");
                            send_ev(ClipboardMessage::SendFormatData(OwnedFormatDataResponse::new_error()));
                        }
                    }
                }
                FromRdp::DataResponse(r) => {
                    let Some((fmt, fds)) = waiting.pop_front() else { continue };
                    if r.is_error() {
                        info!(format = fmt, "client refused the clipboard request");
                        continue;
                    }
                    if r.data().len() > p.max_bytes {
                        p.counters.refused_size.fetch_add(1, Ordering::Relaxed);
                        warn!(bytes = r.data().len(), "client clipboard above clipboard_max_mb; dropped");
                        continue;
                    }
                    let mime_of = source.as_ref().map(|(_, m)| m.clone()).unwrap_or_default();
                    let image = mime_of.iter().any(|(m, f)| *f == fmt && m == "image/png");
                    let html = mime_of.iter().any(|(m, f)| *f == fmt && m == "text/html");
                    let data = if fmt == ClipboardFormatId::CF_UNICODETEXT.value() {
                        Ok(utf16le_to_string(r.data()).into_bytes())
                    } else if html {
                        Ok(cf_html_to_html(r.data()).into_bytes())
                    } else if image && (fmt == ClipboardFormatId::CF_DIB.value() || fmt == ClipboardFormatId::CF_DIBV5.value()) {
                        dib_to_png(r.data())
                    } else {
                        Ok(r.data().to_vec())
                    };
                    match data {
                        Ok(d) => {
                            p.counters.to_host.fetch_add(1, Ordering::Relaxed);
                            std::thread::spawn(move || {
                                for fd in fds {
                                    let mut f = File::from(fd);
                                    let _ = f.write_all(&d);
                                }
                            });
                        }
                        Err(e) => warn!("client clipboard conversion failed: {e:#}"),
                    }
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn text_round_trip() {
        let s = "héllo 你 😀";
        assert_eq!(utf16le_to_string(&string_to_utf16le(s)), s);
    }

    #[test]
    fn html_round_trip() {
        let h = "<b>bold</b> &amp; <i>it</i>";
        let cf = html_to_cf_html(h);
        let txt = String::from_utf8_lossy(&cf);
        assert!(txt.starts_with("Version:0.9\r\nStartHTML:"));
        assert_eq!(cf_html_to_html(&cf), h);
    }

    #[test]
    fn png_dib_png_is_pixel_equal() {
        let (w, h) = (7u32, 5u32);
        let rgba: Vec<u8> = (0..w * h).flat_map(|i| [(i * 9) as u8, (i * 5) as u8, (255 - i) as u8, 255]).collect();
        let mut png_bytes = Vec::new();
        {
            let mut e = png::Encoder::new(&mut png_bytes, w, h);
            e.set_color(png::ColorType::Rgba);
            e.set_depth(png::BitDepth::Eight);
            e.write_header().unwrap().write_image_data(&rgba).unwrap();
        }
        let dib = png_to_dib(&png_bytes).unwrap();
        assert_eq!(dib.len(), 40 + (w * h * 4) as usize);
        let back = dib_to_png(&dib).unwrap();
        let mut d = png::Decoder::new(std::io::Cursor::new(&back)).read_info().unwrap();
        let mut buf = vec![0; d.output_buffer_size().unwrap()];
        d.next_frame(&mut buf).unwrap();
        assert_eq!(buf, rgba);
    }

    #[test]
    fn dib_rejects_garbage() {
        assert!(dib_to_png(&[0u8; 10]).is_err());
        let mut hdr = vec![0u8; 40];
        hdr[0] = 40;
        hdr[4] = 2;
        hdr[8] = 2;
        hdr[14] = 8; // 8 bpp palette: not supported
        assert!(dib_to_png(&hdr).is_err());
    }

    #[test]
    fn formats_both_ways() {
        let f = rdp_formats_for(&["text/plain;charset=utf-8".into(), "image/png".into(), "text/html".into()]);
        assert_eq!(f.len(), 4);
        let m = host_mimes_for(&f);
        assert!(m.iter().any(|(x, id)| x == "UTF8_STRING" && *id == 13));
        assert!(m.iter().any(|(x, id)| x == "image/png" && *id == FMT_PNG));
        assert!(m.iter().any(|(x, id)| x == "text/html" && *id == FMT_HTML));
    }
}
