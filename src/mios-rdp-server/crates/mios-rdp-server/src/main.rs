//! mios-rdp-server: the MiOS native RDP server (rdp-native-design.md). IronRDP (default branch)
//! for the protocol; MiOS glue for capture (ext-image-copy-capture -> GBM dmabuf -> GLES BT.709
//! I420), Cisco OpenH264 (dlopen, slice threads), EGFX AVC420 with damage rects and frame-ack flow
//! control, input (virtual pointer/keyboard, uinput touch/pen mapped to the remote output),
//! clipboard (ext-data-control) and display control (the bridge's display service).
//!
//! Usage:
//!   mios-rdp-server --config native.json          serve (normally via `mios-rdp native`)
//!   mios-rdp-server --self-test [--output NAME]   JSON report: OpenH264, EGL/GBM, Wayland globals
//!   mios-rdp-server --bench-encode WxH FRAMES     OpenH264 timing per thread count (synthetic)
//!   mios-rdp-server --bench-capture NAME SECONDS  capture + GPU conversion timing on an output
#![deny(unsafe_code)]
#![recursion_limit = "512"]

mod auth;
mod capture;
mod clip;
mod config;
mod convert;
mod display;
mod encode;
mod gpu;
mod input;
mod pipeline;
mod redirect;
mod session;
mod stats;
mod tls;
mod uinput;

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use anyhow::{Context as _, Result, anyhow, bail};
use ironrdp_server::{ConnectionPolicy, DesktopSize, RdpServer, ServerAutoReconnect, ServerErrorExt as _};
use serde_json::json;
use tracing::{info, warn};

use crate::config::Config;
use crate::session::Session;

fn setup_logging() {
    use tracing_subscriber::EnvFilter;
    let filter = EnvFilter::try_from_env("MIOS_RDP_LOG")
        .unwrap_or_else(|_| EnvFilter::new("info,ironrdp_server=info,ironrdp_acceptor=warn,ironrdp_connector=warn"));
    let _ = tracing_subscriber::fmt().with_env_filter(filter).with_target(true).with_ansi(false).with_writer(std::io::stderr).try_init();
}

fn main() -> Result<()> {
    setup_logging();
    let args: Vec<String> = std::env::args().collect();
    let get = |k: &str| args.iter().position(|a| a == k).and_then(|i| args.get(i + 1)).cloned();
    if let Some(action) = get("--control") {
        return control_request(&action, get("--socket").map(PathBuf::from));
    }
    if args.iter().any(|a| a == "--version") {
        println!("mios-rdp-server {} (IronRDP server, EGFX AVC420, Cisco OpenH264 via dlopen{})", env!("CARGO_PKG_VERSION"),
                 if cfg!(feature = "test-auth") { ", TEST-AUTH BUILD" } else { "" });
        return Ok(());
    }
    if args.iter().any(|a| a == "--print-defaults") {
        // the code defaults the SSOT [remote.rdp.native|redirect|naming] tables must equal (tests)
        let c = Config::default();
        println!("{}", serde_json::to_string_pretty(&json!({"native": c.native, "redirect": c.redirect, "naming": c.naming}))?);
        return Ok(());
    }
    if args.iter().any(|a| a == "--self-test") {
        return self_test(get("--output"), get("--openh264").unwrap_or_else(|| "auto".into()));
    }
    if let Some(i) = args.iter().position(|a| a == "--bench-encode") {
        let size = args.get(i + 1).cloned().unwrap_or_else(|| "1100x2080".into());
        let frames: u32 = args.get(i + 2).and_then(|f| f.parse().ok()).unwrap_or(120);
        return bench_encode(&size, frames, get("--threads"));
    }
    if let Some(i) = args.iter().position(|a| a == "--bench-capture") {
        let out = args.get(i + 1).cloned().ok_or_else(|| anyhow!("--bench-capture OUTPUT SECONDS"))?;
        let secs: u64 = args.get(i + 2).and_then(|f| f.parse().ok()).unwrap_or(10);
        return bench_capture(&out, secs, get("--hz").and_then(|h| h.parse().ok()).unwrap_or(90), args.iter().any(|a| a == "--encode"), args.iter().any(|a| a == "--bitmap"));
    }
    let cfg_path = PathBuf::from(get("--config").ok_or_else(|| anyhow!("--config native.json is required"))?);
    let cfg = Config::load(&cfg_path)?;
    let rt = tokio::runtime::Builder::new_current_thread().enable_all().build()?;
    let local = tokio::task::LocalSet::new();
    local.block_on(&rt, serve(cfg))
}

fn random_cookie() -> ServerAutoReconnect {
    use rand::RngCore as _;
    let mut r = rand::rng();
    let mut bits = [0u8; 16];
    r.fill_bytes(&mut bits);
    ServerAutoReconnect { logon_id: r.next_u32(), random_bits: bits }
}

#[allow(unsafe_code)]
fn create_listener(cfg: &Config) -> Result<tokio::net::TcpListener> {
    use std::net::ToSocketAddrs;
    use std::os::fd::FromRawFd;

    let addr = cfg
        .listen
        .to_socket_addrs()
        .with_context(|| format!("resolve {}", cfg.listen))?
        .next()
        .ok_or_else(|| anyhow!("no address resolved for {}", cfg.listen))?;

    let is_ipv6 = addr.is_ipv6();
    let domain = if is_ipv6 { libc::AF_INET6 } else { libc::AF_INET };

    // SAFETY: create stream socket with CLOEXEC
    let fd = unsafe { libc::socket(domain, libc::SOCK_STREAM | libc::SOCK_CLOEXEC, 0) };
    if fd < 0 {
        return Err(std::io::Error::last_os_error()).context("socket create");
    }

    struct ScopedFd(libc::c_int);
    impl Drop for ScopedFd {
        fn drop(&mut self) {
            if self.0 >= 0 {
                unsafe { libc::close(self.0) };
            }
        }
    }
    let mut guard = ScopedFd(fd);

    let optval: libc::c_int = 1;
    unsafe {
        libc::setsockopt(
            fd,
            libc::SOL_SOCKET,
            libc::SO_REUSEADDR,
            (&raw const optval).cast(),
            std::mem::size_of::<libc::c_int>() as libc::socklen_t,
        );
        if is_ipv6 {
            let v6only: libc::c_int = 0;
            libc::setsockopt(
                fd,
                libc::IPPROTO_IPV6,
                libc::IPV6_V6ONLY,
                (&raw const v6only).cast(),
                std::mem::size_of::<libc::c_int>() as libc::socklen_t,
            );
        }
    }

    if !cfg.allow_loopback {
        if let Some(ref dev) = cfg.bind_device {
            if !dev.is_empty() {
                if let Ok(c_dev) = std::ffi::CString::new(dev.as_str()) {
                    let r = unsafe {
                        libc::setsockopt(
                            fd,
                            libc::SOL_SOCKET,
                            libc::SO_BINDTODEVICE,
                            c_dev.as_ptr().cast(),
                            c_dev.to_bytes_with_nul().len() as libc::socklen_t,
                        )
                    };
                    if r != 0 {
                        let err = std::io::Error::last_os_error();
                        warn!(dev = %dev, err = %err, "SO_BINDTODEVICE returned error");
                        #[cfg(not(feature = "test-auth"))]
                        return Err(err).with_context(|| format!("SO_BINDTODEVICE to {dev}"));
                    } else {
                        info!(dev = %dev, "bound listener to device");
                    }
                }
            }
        }
    }

    let bind_res = match addr {
        std::net::SocketAddr::V4(v4) => {
            let sin = libc::sockaddr_in {
                sin_family: libc::AF_INET as libc::sa_family_t,
                sin_port: v4.port().to_be(),
                sin_addr: libc::in_addr { s_addr: u32::from_ne_bytes(v4.ip().octets()) },
                sin_zero: [0; 8],
            };
            unsafe {
                libc::bind(fd, (&raw const sin).cast(), std::mem::size_of::<libc::sockaddr_in>() as libc::socklen_t)
            }
        }
        std::net::SocketAddr::V6(v6) => {
            let sin6 = libc::sockaddr_in6 {
                sin6_family: libc::AF_INET6 as libc::sa_family_t,
                sin6_port: v6.port().to_be(),
                sin6_flowinfo: v6.flowinfo(),
                sin6_addr: libc::in6_addr { s6_addr: v6.ip().octets() },
                sin6_scope_id: v6.scope_id(),
            };
            unsafe {
                libc::bind(fd, (&raw const sin6).cast(), std::mem::size_of::<libc::sockaddr_in6>() as libc::socklen_t)
            }
        }
    };

    if bind_res != 0 {
        return Err(std::io::Error::last_os_error()).with_context(|| format!("bind {}", cfg.listen));
    }

    let listen_res = unsafe { libc::listen(fd, 128) };
    if listen_res != 0 {
        return Err(std::io::Error::last_os_error()).context("listen");
    }

    let flags = unsafe { libc::fcntl(fd, libc::F_GETFL, 0) };
    if flags < 0 || unsafe { libc::fcntl(fd, libc::F_SETFL, flags | libc::O_NONBLOCK) } < 0 {
        return Err(std::io::Error::last_os_error()).context("fcntl O_NONBLOCK");
    }

    let raw = guard.0;
    guard.0 = -1;
    let std_listener = unsafe { std::net::TcpListener::from_raw_fd(raw) };
    let listener = tokio::net::TcpListener::from_std(std_listener)?;
    Ok(listener)
}

fn control_request(action: &str, socket: Option<PathBuf>) -> Result<()> {
    use std::io::{Read, Write};
    if !["pause", "resume", "disconnect"].contains(&action) { bail!("unknown remote-session action"); }
    let path = match socket {
        Some(path) => path,
        None => PathBuf::from(std::env::var_os("XDG_RUNTIME_DIR").context("XDG_RUNTIME_DIR is required for session controls")?).join("mios-rdp/control.sock"),
    };
    let mut stream = std::os::unix::net::UnixStream::connect(path).context("remote-session controls are unavailable")?;
    stream.set_read_timeout(Some(Duration::from_secs(3)))?;
    stream.set_write_timeout(Some(Duration::from_secs(3)))?;
    writeln!(stream, "{}", json!({"action": action}))?;
    let mut response = String::new();
    stream.take(4096).read_to_string(&mut response)?;
    let reply: serde_json::Value = serde_json::from_str(&response)?;
    if reply["ok"] != true { bail!("{}", reply["error"].as_str().unwrap_or("remote-session action failed")); }
    println!("{reply}");
    Ok(())
}

fn session_control(s: &Session, action: &str) -> serde_json::Value {
    if !s.attached.load(Ordering::SeqCst) { return json!({"ok":false,"error":"No remote session is connected"}); }
    match action {
        "pause" | "resume" => {
            let paused = action == "pause";
            s.operator_paused.store(paused, Ordering::SeqCst);
            let client_paused = s.conn_suppressed.lock().expect("sup").as_ref().is_some_and(|p| p.load(Ordering::Relaxed));
            s.suppressed.store(paused || client_paused, Ordering::SeqCst);
            let _ = s.input_tx.lock().expect("input").send(input::Cmd::Release);
            s.refresh.store(true, Ordering::SeqCst);
            s.gfx.notify();
            json!({"ok":true,"paused":paused})
        }
        "disconnect" => {
            use ironrdp_pdu::rdp::server_error_info::{ErrorInfo, ProtocolIndependentCode};
            let guard = s.disconnect_handle.lock().expect("disconnect");
            let Some(handle) = guard.as_ref() else { return json!({"ok":false,"error":"The connection is not ready"}) };
            if let Err(e) = handle.disconnect(ErrorInfo::ProtocolIndependentCode(ProtocolIndependentCode::RpcInitiatedDisconnectByuser)) {
                return json!({"ok":false,"error":e.to_string()});
            }
            if let Some(cookie) = s.cookie_handle.lock().expect("cookie").as_ref() { let _ = cookie.set(None); }
            *s.next_cookie.lock().expect("cookie") = None;
            s.operator_paused.store(true, Ordering::SeqCst);
            s.suppressed.store(true, Ordering::SeqCst);
            let _ = s.input_tx.lock().expect("input").send(input::Cmd::Release);
            json!({"ok":true,"disconnected":true})
        }
        _ => json!({"ok":false,"error":"Unknown remote-session action"}),
    }
}

async fn session_controls(s: Arc<Session>) -> Result<()> {
    use std::os::unix::fs::{FileTypeExt, MetadataExt, PermissionsExt};
    use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt};
    let path = s.cfg.stats_path.with_file_name("control.sock");
    let parent = path.parent().context("control socket has no parent")?;
    std::fs::create_dir_all(parent)?;
    let meta = std::fs::metadata(parent)?;
    if meta.uid() != s.cfg.operator_uid || meta.permissions().mode() & 0o077 != 0 { bail!("remote control directory must be private to the operator"); }
    if let Ok(meta) = std::fs::symlink_metadata(&path) {
        if !meta.file_type().is_socket() || meta.uid() != s.cfg.operator_uid { bail!("unexpected remote control socket owner or type"); }
        if std::os::unix::net::UnixStream::connect(&path).is_ok() { bail!("remote control socket is already active"); }
        std::fs::remove_file(&path)?;
    }
    let listener = tokio::net::UnixListener::bind(&path)?;
    std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600))?;
    loop {
        let (mut stream, _) = listener.accept().await?;
        if stream.peer_cred()?.uid() != s.cfg.operator_uid { continue; }
        let mut line = String::new();
        let read = tokio::time::timeout(Duration::from_secs(2), tokio::io::BufReader::new((&mut stream).take(1024)).read_line(&mut line)).await;
        let reply = match read {
            Ok(Ok(_)) if line.ends_with('\n') => match serde_json::from_str::<serde_json::Value>(&line) {
                Ok(value) => session_control(&s, value["action"].as_str().unwrap_or("")),
                Err(_) => json!({"ok":false,"error":"Invalid request"}),
            },
            _ => json!({"ok":false,"error":"Incomplete request"}),
        };
        let _ = stream.write_all(format!("{reply}\n").as_bytes()).await;
    }
}

async fn serve(cfg: Config) -> Result<()> {
    if cfg.auth == "test" && !cfg!(feature = "test-auth") {
        bail!("auth = \"test\" exists only in test-auth builds");
    }
    // TLS: the existing certificate; the key comes from mios-rdp-priv as a descriptor
    let (cert, mut key) = if cfg.tls_source == "priv" {
        let sock = cfg.priv_socket.clone();
        tokio::task::spawn_blocking(move || auth::priv_tls(&sock)).await??
    } else {
        (std::fs::read(&cfg.cert_file).context("cert_file")?, std::fs::read(&cfg.key_file).context("key_file")?)
    };
    let (acceptor, fingerprint) = tls::acceptor(&cert, &key, &cfg.tls_protocols)?;
    key.iter_mut().for_each(|b| *b = 0);
    std::hint::black_box(&key);
    drop(key);
    info!(sha256 = %fingerprint, protocols = %cfg.tls_protocols, "TLS certificate loaded");

    let display = display::DisplayClient::spawn(&cfg.display_service, &cfg.display_config)?;
    let bitmap = Arc::new(pipeline::BitmapSlot::default());
    let (input_tx, input_rx) = std::sync::mpsc::channel();
    let authenticated = Arc::new(AtomicBool::new(false));
    let desktop = Arc::new(AtomicU64::new((1280u64 << 32) | 720));
    let peer: auth::CurrentPeer = Arc::default();
    let backend = match cfg.auth.as_str() {
        "priv" => auth::Backend::Priv(cfg.priv_socket.clone()),
        #[cfg(feature = "test-auth")]
        "test" => auth::Backend::Test { user: cfg.test_user.clone(), password: auth::read_secret(&cfg.test_password_file)? },
        other => bail!("auth = {other:?} (priv)"),
    };
    let validator = Arc::new(auth::Validator::new(backend, Arc::clone(&peer), Duration::from_secs(cfg.native.auth_backoff_seconds)));
    let input_counters = Arc::new(input::InputCounters::default());
    let clip_counters = Arc::new(clip::ClipCounters::default());
    let s = Arc::new(Session {
        cfg: cfg.clone(),
        display: Arc::clone(&display),
        gfx: Arc::new(pipeline::Gfx::default()),
        stats: Arc::new(pipeline::PipeStats::default()),
        authenticated: Arc::clone(&authenticated),
        conn_suppressed: Mutex::new(None),
        suppressed: Arc::new(AtomicBool::new(false)),
        operator_paused: Arc::new(AtomicBool::new(false)),
        desktop: Arc::clone(&desktop),
        layout: Arc::new(AtomicU32::new(0)),
        capture_hz: Arc::new(AtomicU32::new(cfg.target_hz)),
        refresh: Arc::new(AtomicBool::new(false)),
        input_tx: Mutex::new(input_tx.clone()),
        input_counters: Arc::clone(&input_counters),
        clip_counters: Arc::clone(&clip_counters),
        capture: Mutex::new(None),
        capture_info: Mutex::new(None),
        capture_stats: Mutex::new(None),
        pipe: Mutex::new(None),
        bitmap: Arc::clone(&bitmap),
        mailbox: Mutex::new(None),
        applied: Mutex::new(None),
        attached: AtomicBool::new(false),
        peer: Arc::clone(&peer),
        client: Mutex::new(json!({})),
        connected_at: Mutex::new(None),
        out_hz: AtomicU32::new(cfg.target_hz),
        user: Arc::clone(&validator.last_user),
        cookie_handle: Mutex::new(None),
        disconnect_handle: Mutex::new(None),
        next_cookie: Mutex::new(None),
        arc_rejected: AtomicBool::new(false),
    });

    let controls = Arc::clone(&s);
    tokio::task::spawn_local(async move {
        if let Err(e) = session_controls(controls).await { warn!("remote-session controls unavailable: {e:#}"); }
    });

    if cfg.input {
        let _input = input::spawn(
            input::InputParams {
                output: cfg.virtual_output.clone(),
                layout: cfg.keyboard_layout.clone(),
                touch_name: cfg.naming.touch_device.clone(),
                pen_name: cfg.naming.pen_device.clone(),
                touch: cfg.redirect.touch,
                pen: cfg.redirect.pen,
                desktop: Arc::clone(&desktop),
                display: Arc::clone(&display),
                counters: Arc::clone(&input_counters),
                operator_paused: Arc::clone(&s.operator_paused),
            },
            input_rx,
        );
    }

    #[cfg(feature = "test-auth")]
    if !cfg.test_input_fifo.as_os_str().is_empty() {
        test_input_feed(cfg.test_input_fifo.clone(), input_tx.clone(), Arc::clone(&s));
    }

    let gfx_factory = pipeline::GfxFactory { gfx: Arc::clone(&s.gfx), max_in_flight: cfg.native.frames_in_flight };
    let builder = ironrdp_server::RdpServer::builder()
        .with_addr(cfg.listen.parse::<std::net::SocketAddr>().context("listen")?)
        .with_tls(acceptor);
    let builder = if cfg.input {
        builder.with_input_handler(input::InputHandler { tx: input_tx.clone() })
    } else {
        builder.with_no_input()
    };
    let mut builder = builder
        .with_display_handler(session::Display { s: Arc::clone(&s) })
        .with_gfx_factory(Some(Box::new(gfx_factory)))
        .with_credential_validator(Some(validator.clone() as Arc<dyn ironrdp_server::CredentialValidator>))
        .with_connection_handler(Some(Box::new(session::Hooks { s: Arc::clone(&s) })))
        .with_connection_policy(ConnectionPolicy::Reject)
        .with_honor_client_desktop_size(Some(DesktopSize { width: cfg.native.max_width, height: cfg.native.max_height }));
    // Legacy bitmap codecs for clients that negotiate EGFX without AVC420. Microsoft's Windows App on
    // iOS/macOS sets AVC_DISABLED in every EGFX capability set and lists only NSCodec in its bitmap-codec
    // capability, so with IronRDP's default list (RemoteFX, QOI, QOIZ) it got raw/RLE bitmap updates:
    // several MB per 1100x2080 frame and TCP back-pressure on the phone link (mios, 2026-10-23).
    // IronRDP ranks NSCodec below RemoteFX/QOI, so clients that offer those keep them.
    {
        use ironrdp_pdu::rdp::capability_sets::{Codec, CodecProperty, NsCodec, server_codecs_capabilities};
        let mut codecs = server_codecs_capabilities(&[]).map_err(|e| anyhow!("server bitmap codecs: {e}"))?;
        codecs.0.push(Codec {
            id: 0,
            property: CodecProperty::NsCodec(NsCodec {
                is_dynamic_fidelity_allowed: true,
                is_subsampling_allowed: true,
                color_loss_level: 3,
            }),
        });
        builder = builder.with_bitmap_codecs(codecs);
    }
    if cfg.input && (cfg.redirect.touch || cfg.redirect.pen) {
        builder = builder.with_rdpei_factory(Some(Box::new(input::RdpeiFactory {
            tx: Mutex::new(input_tx.clone()),
            touch: cfg.redirect.touch,
            pen: cfg.redirect.pen,
        })));
    }
    if cfg.redirect.clipboard != "off" {
        let shared = clip::spawn(clip::Params {
            direction: cfg.redirect.clipboard.clone(),
            max_bytes: (cfg.redirect.clipboard_max_mb as usize).saturating_mul(1024 * 1024),
            authenticated: Arc::clone(&authenticated),
            counters: Arc::clone(&clip_counters),
        })?;
        builder = builder.with_cliprdr_factory(Some(Box::new(clip::Factory { shared })));
    }
    if cfg.redirect.audio_output {
        builder = builder.with_sound_factory(Some(Box::new(redirect::SoundFactory::new())));
    }
    if should_enable_microphone(&cfg) {
        builder = builder.with_rdpeai_factory(Some(Box::new(redirect::RdpeaiFactory::new())));
    }
    if cfg.redirect.drives {
        builder = builder.with_rdpdr_factory(Some(Box::new(redirect::RdpdrFactory::new(&cfg.runtime_dir))));
    }
    let _cam = if cfg.redirect.camera { Some(redirect::CameraManager::new(true)) } else { None };
    let _loc = if cfg.redirect.location { Some(redirect::LocationWriter::new()) } else { None };
    redirect::log_redirections(&cfg);
    if cfg.native.auto_reconnect {
        builder = builder.with_auto_reconnect_cookie(Some(random_cookie()));
    }
    let mut server: RdpServer = builder.build();
    if cfg.native.auto_reconnect {
        server.set_auto_reconnect_on_request(true);
    }
    *s.cookie_handle.lock().expect("ch") = Some(server.auto_reconnect_cookie_handle());
    *s.disconnect_handle.lock().expect("dh") = Some(server.error_info_disconnect_handle());
    server.enable_autodetect();
    info!(
        listen = %cfg.listen,
        output = %cfg.virtual_output,
        target_hz = cfg.target_hz,
        clipboard = %cfg.redirect.clipboard,
        touch = cfg.redirect.touch,
        pen = cfg.redirect.pen,
        auth = %cfg.auth,
        "mios-rdp-server listening"
    );
    let listener = create_listener(&cfg)?;

    let stats_task = tokio::task::spawn_local(stats_loop(Arc::clone(&s)));
    let mut term = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())?;
    let mut last_drop: Option<Instant> = None;
    let res: Result<()> = async {
        loop {
            let (stream, peer_addr) = tokio::select! {
                a = listener.accept() => a?,
                _ = term.recv() => break Ok(()),
                _ = tokio::signal::ctrl_c() => break Ok(()),
            };
            if !session::peer_ok(&cfg, peer_addr.ip()) {
                info!(peer = %peer_addr, "refused before X.224: source outside the tailnet/LAN/loopback allow-list");
                drop(stream);
                continue;
            }
        session::tune_socket(&stream, &cfg.native);
            if cfg.native.auto_reconnect {
                if let Some(t) = last_drop {
                    if t.elapsed() > Duration::from_secs(cfg.native.auto_reconnect_seconds) {
                        server.set_auto_reconnect_cookie(Some(random_cookie())); // expired: the old cookie no longer resumes
                    }
                }
            }
            s.begin_connection(peer_addr);
            server.set_connection_local_addr(stream.local_addr().ok());
            info!(peer = %peer_addr, "connection");
            let r = {
                let fut = server.run_connection(stream);
                tokio::pin!(fut);
                loop {
                    tokio::select! {
                        r = &mut fut => break r,
                        a = listener.accept() => {
                            if let Ok((s2, p2)) = a {
                                info!(peer = %p2, "refused: a session is live (ConnectionPolicy::Reject)");
                                drop(s2);
                            }
                        }
                    }
                }
            };
            let s2 = Arc::clone(&s);
            let rr = r.as_ref().map(|_| ()).map_err(|e| ironrdp_server::ServerError::reason("connection", format!("{e:#}")));
            tokio::task::spawn_blocking(move || s2.end_connection(&rr)).await?;
            if cfg.native.auto_reconnect {
                if r.is_ok() || s.arc_rejected.load(Ordering::SeqCst) {
                    server.set_auto_reconnect_cookie(Some(random_cookie())); // clean logoff or rejected ARC: cookie invalidated
                    last_drop = None;
                } else {
                    if let Some(c) = s.next_cookie.lock().expect("cookie").take() {
                        // Single-use ARC enforcement: install only the rotated cookie and clear previous_auto_reconnect_cookie
                        server.set_auto_reconnect_cookie(Some(c));
                    }
                    last_drop = Some(Instant::now());
                }
            }
        }
    }
    .await;
    stats_task.abort();
    info!("stopping");
    let d = Arc::clone(&display);
    let _ = tokio::task::spawn_blocking(move || d.shutdown()).await;
    let _ = input_tx.send(input::Cmd::Stop);
    stats::write_atomic(&cfg.stats_path, &json!({"server": "native", "attached": false, "time": now_epoch(), "stopped": true}));
    res
}

/// test-auth builds: synthetic RDPEI frames (tests T1/T2) from a FIFO, delivered exactly like the RDPEI
/// handler does (Cmd::Touch / Cmd::Pen), only while an authenticated session is attached.
#[cfg(feature = "test-auth")]
fn test_input_feed(path: PathBuf, tx: std::sync::mpsc::Sender<input::Cmd>, s: Arc<Session>) {
    use ironrdp_server::{PenContact, PenContactFlags as PF, PenEventPdu, PenFlags, PenFrame, TouchContact,
                         TouchContactFlags as TF, TouchEventPdu, TouchFrame};
    use std::io::BufRead as _;
    std::thread::spawn(move || loop {
        let Ok(f) = std::fs::File::open(&path) else { return };
        for line in std::io::BufReader::new(f).lines() {
            let Ok(line) = line else { break };
            let Ok(v) = serde_json::from_str::<serde_json::Value>(&line) else { continue };
            if !s.attached.load(Ordering::SeqCst) {
                warn!("test input ignored: no authenticated session");
                continue;
            }
            if let Some(cs) = v["touch"].as_array() {
                let contacts = cs.iter().filter_map(|c| {
                    let a = c.as_array()?;
                    let flags = match a.get(3)?.as_str()? {
                        "down" => TF::DOWN | TF::INRANGE | TF::INCONTACT,
                        "update" => TF::UPDATE | TF::INRANGE | TF::INCONTACT,
                        "up" => TF::UP,
                        _ => TF::CANCELED,
                    };
                    Some(TouchContact::new(a[0].as_u64()? as u8, a[1].as_i64()? as i32, a[2].as_i64()? as i32, flags))
                }).collect();
                let _ = tx.send(input::Cmd::Touch(TouchEventPdu::new(0, vec![TouchFrame::new(0, contacts)])));
            }
            if let Some(a) = v["pen"].as_array() {
                let (Some(x), Some(y), Some(k)) = (a.first().and_then(|v| v.as_i64()), a.get(1).and_then(|v| v.as_i64()), a.get(2).and_then(|v| v.as_str())) else { continue };
                let flags = match k {
                    "down" => PF::DOWN | PF::INRANGE | PF::INCONTACT,
                    "update" => PF::UPDATE | PF::INRANGE | PF::INCONTACT,
                    "hover" => PF::UPDATE | PF::INRANGE,
                    _ => PF::UP,
                };
                let mut pf = PenFlags::empty();
                match a.get(6).and_then(|v| v.as_str()) {
                    Some("eraser") => pf |= PenFlags::ERASER_PRESSED,
                    Some("barrel") => pf |= PenFlags::BARREL_PRESSED,
                    _ => {}
                }
                let c = PenContact::new(0, x as i32, y as i32, flags)
                    .with_pen_flags(pf)
                    .with_pressure(a.get(3).and_then(|v| v.as_u64()).unwrap_or(0) as u32)
                    .with_tilt(a.get(4).and_then(|v| v.as_i64()).unwrap_or(0) as i16, a.get(5).and_then(|v| v.as_i64()).unwrap_or(0) as i16);
                let _ = tx.send(input::Cmd::Pen(PenEventPdu::new(0, vec![PenFrame::new(0, vec![c])])));
            }
            if v["rdpei_closed"].as_bool() == Some(true) {
                let _ = tx.send(input::Cmd::RdpeiClosed);
            }
        }
    });
}

fn now_epoch() -> f64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

async fn stats_loop(s: Arc<Session>) {
    let mut iv = tokio::time::interval(Duration::from_secs_f64(stats::WINDOW_SECS));
    let mut prev = (0u64, 0u64, 0u64, 0u64, 0u64, 0u64, 0u64, 0u64);
    let mut prev_cpu = stats::process_cpu_seconds();
    let mut prev_t = Instant::now();
    let mut busy_windows = 0u32;
    let mut guard_hz: Option<u32> = None;
    loop {
        iv.tick().await;
        let now = Instant::now();
        let dt = now.duration_since(prev_t).as_secs_f64().max(0.001);
        prev_t = now;
        let st = &s.stats;
        let acked = s.gfx.acks.lock().expect("acks").acked;
        let cur = (
            st.captured.load(Ordering::Relaxed),
            st.encoded.load(Ordering::Relaxed),
            acked,
            st.bytes.load(Ordering::Relaxed),
            st.skipped_static.load(Ordering::Relaxed),
            st.bitmap_frames.load(Ordering::Relaxed),
            st.skipped_rc.load(Ordering::Relaxed),
            st.bitmap_delivered.load(Ordering::Relaxed),
        );
        let rate = |a: u64, b: u64| (a.saturating_sub(b)) as f64 / dt;
        // EGFX client acks/s + completed bitmap encoder/socket-write operations/s.
        // Bitmap has no display acknowledgement; these are not measured phone scanouts.
        // Bitmap frames queued but not drained are reported separately (bitmap_queued_fps).
        let fps = rate(cur.2, prev.2) + rate(cur.7, prev.7);
        let doc_core = (rate(cur.0, prev.0), rate(cur.1, prev.1), fps, rate(cur.3, prev.3) * 8.0 / 1000.0, rate(cur.4, prev.4), rate(cur.6, prev.6), rate(cur.5, prev.5), rate(cur.7, prev.7));
        prev = cur;
        let cpu = stats::process_cpu_seconds();
        let cpu_pct = 100.0 * (cpu - prev_cpu) / dt;
        prev_cpu = cpu;
        let samples = std::mem::take(&mut *st.samples.lock().expect("samples"));
        let (ack_ms, glass_ms) = {
            let mut a = s.gfx.acks.lock().expect("acks");
            (std::mem::take(&mut a.ack_ms), std::mem::take(&mut a.glass_ms))
        };
        let attached = s.attached.load(Ordering::SeqCst);
        if let Some(cs) = s.conn_suppressed.lock().expect("sup").as_ref() {
            s.suppressed.store(s.operator_paused.load(Ordering::SeqCst) || cs.load(Ordering::Relaxed), Ordering::Relaxed);
        }
        let applied = s.applied.lock().expect("applied").clone();
        let mut out_hz = s.out_hz.load(Ordering::Relaxed);
        if attached && s.cfg.refresh_pacing == "adaptive" {
            let d = Arc::clone(&s.display);
            if let Ok(Ok(hz)) = tokio::task::spawn_blocking(move || d.pace(fps)).await {
                if hz > 0 {
                    out_hz = hz;
                    s.out_hz.store(hz, Ordering::Relaxed);
                }
            }
        }
        // Adaptive refresh pacer capping when GPU is busy to keep HDMI-A-1 at 100-144 FPS
        if attached {
            let busy = stats::gpu_busy_percent().unwrap_or(0);
            if busy >= 80 {
                let cap = (fps.ceil() as u32).clamp(30, 60);
                if out_hz > cap {
                    out_hz = cap;
                    s.out_hz.store(cap, Ordering::Relaxed);
                    let d = Arc::clone(&s.display);
                    let _ = tokio::task::spawn_blocking(move || d.refresh(cap, "gpu_busy_cap")).await;
                }
            }
        }
        // gpu_guard (opt-in): favour the local outputs while the iGPU is saturated
        if attached && s.cfg.native.gpu_guard {
            let busy = stats::gpu_busy_percent().unwrap_or(0);
            busy_windows = if busy >= 90 { busy_windows + 1 } else { 0 };
            if busy_windows >= 3 {
                busy_windows = 0;
                let cur_hz = guard_hz.unwrap_or(out_hz);
                let next = cur_hz.saturating_sub(15).max(s.cfg.native.gpu_guard_min_hz);
                if next < cur_hz {
                    guard_hz = Some(next);
                    s.capture_hz.store(next, Ordering::SeqCst);
                    let d = Arc::clone(&s.display);
                    let _ = tokio::task::spawn_blocking(move || d.refresh(next, "gpu_guard")).await;
                }
            } else if busy < 70 && guard_hz.is_some() {
                guard_hz = None;
                s.capture_hz.store(s.cfg.target_hz, Ordering::SeqCst);
            }
        }
        // Bitmap has no acks. Include encoder/write completion and current stalled-write
        // age: a newest-frame slot stays fresh even when TCP is blocked for seconds.
        if attached && st.bitmap_mode.load(Ordering::Relaxed) {
            let queue_us = stats::percentile_u64(&samples.capture_to_send_us, 50.0) as u64;
            let write_us = stats::percentile_u64(&samples.bitmap_write_us, 50.0) as u64;
            let pending_us = st.bitmap_write.lock().expect("bitmap write").age_us(Instant::now());
            let p50_us = queue_us.max(write_us).max(pending_us);
            let slow_us = (s.cfg.native.bitmap_slow_queue_ms * 1000.0) as u64;
            let cur_hz = s.capture_hz.load(Ordering::Relaxed);
            let next = pipeline::bitmap_pace(cur_hz, p50_us, slow_us, s.cfg.native.bitmap_min_hz, s.cfg.native.bitmap_hz);
            if next != cur_hz {
                s.capture_hz.store(next, Ordering::SeqCst);
                info!(from_hz = cur_hz, to_hz = next, queue_ms_p50 = p50_us as f64 / 1000.0, "bitmap pacing stepped the capture rate");
            }
        }
        let ci = s.capture_info.lock().expect("ci").as_ref().map(|c| c.lock().expect("ci").clone());
        let advertised = s.gfx.advertised.lock().expect("gfx").clone();
        let negotiated = s.gfx.negotiated.lock().expect("gfx").clone();
        let level = *st.last_level.lock().expect("lvl");
        let surface = *st.surface.lock().expect("surface");
        let ic = &s.input_counters;
        let cc = &s.clip_counters;
        let doc = json!({
            "server": "native",
            "attached": attached,
            "control_supported": s.cfg.stats_path.with_file_name("control.sock").exists(),
            "paused": s.operator_paused.load(Ordering::SeqCst),
            "output": applied.as_ref().map(|a| a.output.clone()).unwrap_or_else(|| s.cfg.virtual_output.clone()),
            "width": applied.as_ref().map(|a| a.width),
            "height": applied.as_ref().map(|a| a.height),
            "scale": applied.as_ref().map(|a| a.scale),
            "target_hz": s.cfg.target_hz,
            "output_hz": out_hz,
            "pacing": s.cfg.refresh_pacing,
            "gpu_guard": s.cfg.native.gpu_guard,
            "fps": (doc_core.2 * 10.0).round() / 10.0,
            "captured_fps": (doc_core.0 * 10.0).round() / 10.0,
            "encoded_fps": (doc_core.1 * 10.0).round() / 10.0,
            "static_skipped_fps": (doc_core.4 * 10.0).round() / 10.0,
            "rc_skipped_fps": (doc_core.5 * 10.0).round() / 10.0,
            "bitmap_queued_fps": (doc_core.6 * 10.0).round() / 10.0,
            "bitmap_delivered_fps": (doc_core.7 * 10.0).round() / 10.0,
            "bitmap_fps_basis": "socket_write_complete",
            "bitmap_damage_threshold": s.cfg.native.bitmap_damage_threshold,
            "bitmap_write_ms_p50": stats::percentile_u64(&samples.bitmap_write_us,50.0)/1000.0,
            "bitmap_capture_to_write_ms_p50": stats::percentile_u64(&samples.bitmap_capture_to_write_us,50.0)/1000.0,
            "bitmap_write_in_flight_ms": st.bitmap_write.lock().expect("bitmap write").age_us(Instant::now()) as f64/1000.0,
            "bitmap_stale_dropped": st.bitmap_stale_dropped.load(Ordering::Relaxed),
            "bitmap_hz": s.cfg.native.bitmap_hz,
            "bitmap_capture_hz": s.capture_hz.load(Ordering::Relaxed),
            "bitrate_kbps": doc_core.3.round(),
            "target_bitrate_kbps": st.bitrate_bps.load(Ordering::Relaxed) / 1000,
            "frames_in_flight": st.frames_in_flight.load(Ordering::Relaxed),
            "backpressured": st.backpressured.load(Ordering::Relaxed),
            "idr": st.idr.load(Ordering::Relaxed),
            "refined": st.refined.load(Ordering::Relaxed),
            "codec": active_codec(s.gfx.avc420.load(Ordering::Relaxed), cur.1, cur.5, st.bitmap_mode.load(Ordering::Relaxed)),
            "h264_profile_level": level.map(|(p, l)| json!({"profile_idc": p, "level_idc": l})),
            "slices": st.last_slices.load(Ordering::Relaxed),
            "surface": [surface.0, surface.1],
            "encode_ms_p50": stats::percentile_u64(&samples.encode_us, 50.0) / 1000.0,
            "encode_ms_p95": stats::percentile_u64(&samples.encode_us, 95.0) / 1000.0,
            "convert_ms_p50": stats::percentile_u64(&samples.convert_us, 50.0) / 1000.0,
            "capture_to_send_ms_p50": stats::percentile_u64(&samples.capture_to_send_us, 50.0) / 1000.0,
            "capture_to_send_ms_p95": stats::percentile_u64(&samples.capture_to_send_us, 95.0) / 1000.0,
            "ack_ms_p50": stats::percentile(&ack_ms, 50.0),
            "ack_ms_p95": stats::percentile(&ack_ms, 95.0),
            "capture_to_ack_ms_p50": stats::percentile(&glass_ms, 50.0),
            "damage_pct_p50": stats::percentile(&samples.damage_pct, 50.0),
            "cpu_pct": (cpu_pct * 10.0).round() / 10.0,
            "rss_mb": (stats::rss_mb() * 10.0).round() / 10.0,
            "rtt_ms": if ack_ms.is_empty() { None } else { Some(stats::percentile(&ack_ms, 50.0).round() as u32) },
            "negotiated": {"egfx_advertised": advertised, "egfx_negotiated": negotiated,
                           "avc420": s.gfx.avc420.load(Ordering::Relaxed), "avc444": s.gfx.avc444.load(Ordering::Relaxed)},
            "capture": ci,
            "client": s.client.lock().expect("client").clone(),
            "input": {"keys": ic.keys.load(Ordering::Relaxed), "unicode": ic.unicode.load(Ordering::Relaxed),
                      "pointer": ic.pointer.load(Ordering::Relaxed), "touch": ic.touch.load(Ordering::Relaxed),
                      "pen": ic.pen.load(Ordering::Relaxed), "touch_fallback": ic.touch_fallback.load(Ordering::Relaxed),
                      "last_touch_us": ic.last_touch_us.load(Ordering::Relaxed)},
            "clipboard": {"to_client": cc.to_client.load(Ordering::Relaxed), "to_host": cc.to_host.load(Ordering::Relaxed),
                          "refused_secret": cc.refused_secret.load(Ordering::Relaxed), "refused_size": cc.refused_size.load(Ordering::Relaxed),
                          "refused_direction": cc.refused_direction.load(Ordering::Relaxed)},
            "time": now_epoch(),
        });
        stats::write_atomic(&s.cfg.stats_path, &doc);
    }
}

fn self_test(output: Option<String>, lib: String) -> Result<()> {
    let mut rep = serde_json::Map::new();
    // OpenH264: load + encode one 1104x2080 IDR
    match encode::OpenH264::new(
        &lib,
        encode::EncoderSettings {
            width: 1104,
            height: 2080,
            fps: 90.0,
            bitrate_bps: 8_000_000,
            max_bitrate_bps: 25_000_000,
            qp_min: 18,
            qp_max: 40,
            threads: 4,
            complexity: "low".into(),
            full_range: true,
        },
    ) {
        Ok(mut e) => {
            use encode::H264Encoder as _;
            let stride = 1104u32;
            let data = vec![128u8; (stride * 2080 * 3 / 2) as usize];
            let pic = encode::I420 { data: &data, width: 1104, height: 2080, stride };
            match e.encode(&pic, 0) {
                Ok(o) => {
                    rep.insert(
                        "openh264".into(),
                        json!({"ok": true, "library": e.library, "version": e.version, "idr": o.idr, "bytes": o.data.len(),
                                "profile_level": encode::sps_profile_level(&o.data), "slices": encode::slice_count(&o.data), "encode_ms": o.encode_us as f64 / 1000.0}),
                    );
                }
                Err(err) => {
                    rep.insert("openh264".into(), json!({"ok": false, "error": format!("{err:#}")}));
                }
            }
        }
        Err(err) => {
            rep.insert("openh264".into(), json!({"ok": false, "error": format!("{err:#}")}));
        }
    }
    // EGL/GBM on the render node
    let node = capture::default_render_node();
    rep.insert("render_node".into(), json!(node.as_ref().map(|p| p.display().to_string())));
    if let Some(n) = node {
        let r = (|| -> Result<serde_json::Value> {
            let f = std::fs::OpenOptions::new().read(true).write(true).open(&n)?;
            let g = gbm::Device::new(f)?;
            let raw = gbm::AsRaw::as_raw(&g) as *mut std::ffi::c_void;
            let ctx = gpu::Gpu::new(raw, gpu::Matrix::Bt709Full, true)?;
            let mods = ctx.importable_modifiers(gpu::DRM_FORMAT_XRGB8888);
            Ok(json!({"ok": true, "renderer": ctx.renderer, "low_priority_context": ctx.low_priority, "xrgb8888_modifiers": mods.len()}))
        })();
        rep.insert("egl".into(), r.unwrap_or_else(|e| json!({"ok": false, "error": format!("{e:#}")})));
    }
    // Wayland globals (no devices, no nodes, no capture session)
    let r = (|| -> Result<serde_json::Value> {
        use wayland_client::Connection;
        let conn = Connection::connect_to_env()?;
        let (globals, _q) = wayland_client::globals::registry_queue_init::<SelfTestState>(&conn)?;
        let names: Vec<String> = globals.contents().clone_list().into_iter().map(|g| g.interface).collect();
        let want = [
            "ext_output_image_capture_source_manager_v1",
            "ext_image_copy_capture_manager_v1",
            "zwp_linux_dmabuf_v1",
            "zwlr_virtual_pointer_manager_v1",
            "zwp_virtual_keyboard_manager_v1",
            "ext_data_control_manager_v1",
        ];
        let have: serde_json::Map<String, serde_json::Value> =
            want.iter().map(|w| ((*w).to_owned(), json!(names.iter().any(|n| n == w)))).collect();
        Ok(json!({"ok": have.values().all(|v| v == &json!(true)), "globals": have, "all": names}))
    })();
    rep.insert("wayland".into(), r.unwrap_or_else(|e| json!({"ok": false, "error": format!("{e:#}")})));
    rep.insert("uinput".into(), json!(std::fs::OpenOptions::new().write(true).open("/dev/uinput").is_ok()));
    rep.insert("test_auth_build".into(), json!(cfg!(feature = "test-auth")));
    let mut probe_cfg = Config::default();
    probe_cfg.redirect.microphone = false;
    let mic_gate_ok = !should_enable_microphone(&probe_cfg);
    let gfx_probe = pipeline::Gfx::default();
    gfx_probe.ready.store(true, Ordering::SeqCst);
    gfx_probe.avc420.store(true, Ordering::SeqCst);
    let avc_gate_ok = pipeline::check_avc420_ready(&gfx_probe);
    rep.insert(
        "invariants".into(),
        json!({
            "ok": mic_gate_ok && avc_gate_ok,
            "avc420_ready_gate": avc_gate_ok,
            "microphone_redirect_gate": mic_gate_ok,
        }),
    );
    if let Some(o) = output {
        rep.insert("output".into(), json!(o));
    }
    println!("{}", serde_json::to_string_pretty(&serde_json::Value::Object(rep))?);
    Ok(())
}

pub(crate) fn should_enable_microphone(cfg: &Config) -> bool {
    if cfg.redirect.microphone {
        return true;
    }
    false
}

pub(crate) fn active_codec(avc420: bool, encoded: u64, bitmap_frames: u64, bitmap_mode: bool) -> &'static str {
    if bitmap_mode || (bitmap_frames > 0 && encoded == 0) {
        "bitmap"
    } else if avc420 && encoded > 0 {
        "avc420"
    } else if bitmap_frames > 0 {
        "bitmap"
    } else {
        "none"
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn microphone_redirect_gate_respects_config() {
        let mut cfg = Config::default();
        cfg.redirect.microphone = false;
        assert!(!should_enable_microphone(&cfg), "microphone=false must not enable RDPEAI factory");
        cfg.redirect.microphone = true;
        assert!(should_enable_microphone(&cfg), "microphone=true must enable RDPEAI factory");
    }

    #[test]
    fn active_codec_reports_bitmap_when_forced_to_bitmap_mode() {
        // When EGFX negotiates AVC420 (avc420=true) but the pipeline is in bitmap_mode (encoded=0, bitmap_frames>0),
        // stats.json must report "bitmap", never "avc420".
        assert_eq!(active_codec(true, 0, 25, true), "bitmap");
        assert_eq!(active_codec(true, 0, 25, false), "bitmap");
        assert_eq!(active_codec(true, 100, 0, false), "avc420");
        assert_eq!(active_codec(false, 0, 10, false), "bitmap");
        assert_eq!(active_codec(true, 0, 0, false), "none");
    }
}

struct SelfTestState;
impl wayland_client::Dispatch<wayland_client::protocol::wl_registry::WlRegistry, wayland_client::globals::GlobalListContents>
    for SelfTestState
{
    fn event(
        _: &mut Self,
        _: &wayland_client::protocol::wl_registry::WlRegistry,
        _: wayland_client::protocol::wl_registry::Event,
        _: &wayland_client::globals::GlobalListContents,
        _: &wayland_client::Connection,
        _: &wayland_client::QueueHandle<Self>,
    ) {
    }
}

/// Synthetic screen-like content: a static UI with a moving block + a scrolling text band.
fn synth_frame(buf: &mut [u8], w: u32, h: u32, stride: u32, t: u32, full_motion: bool) {
    let (w, h, s) = (w as usize, h as usize, stride as usize);
    for y in 0..h {
        for x in 0..w {
            let v = if full_motion {
                ((x + y + t as usize * 7) % 256) as u8
            } else {
                let block = x >= (t as usize * 13) % (w - 200) && x < (t as usize * 13) % (w - 200) + 200 && y >= 600 && y < 800;
                let text = (y / 24) % 2 == 0 && (x / 8 + y / 24 + t as usize) % 3 == 0 && (900..1500).contains(&y);
                if block { 30 } else if text { 230 } else { 120 + ((x / 64 + y / 64) % 2) as u8 * 20 }
            };
            buf[y * s + x] = v;
        }
    }
    let base = s * h;
    for cy in 0..h / 2 {
        for cx in 0..w / 2 {
            buf[base + cy * s + cx] = 128;
            buf[base + cy * s + s / 2 + cx] = if full_motion { ((cx + t as usize) % 256) as u8 } else { 128 };
        }
    }
}

fn bench_encode(size: &str, frames: u32, threads: Option<String>) -> Result<()> {
    use encode::H264Encoder as _;
    let (w, h) = size.split_once('x').map(|(a, b)| (a.parse::<u32>(), b.parse::<u32>())).ok_or_else(|| anyhow!("WxH"))?;
    let (w, h) = (w?, h?);
    let stride = w.div_ceil(16) * 16;
    let tlist: Vec<u16> = threads.map_or(vec![1, 2, 4], |t| t.split(',').filter_map(|x| x.parse().ok()).collect());
    let mut out = Vec::new();
    for full in [false, true] {
        for &t in &tlist {
            let mut e = encode::OpenH264::new(
                "auto",
                encode::EncoderSettings {
                    width: w,
                    height: h,
                    fps: 90.0,
                    bitrate_bps: 8_000_000,
                    max_bitrate_bps: 25_000_000,
                    qp_min: 18,
                    qp_max: 40,
                    threads: t,
                    complexity: "low".into(),
                    full_range: true,
                },
            )?;
            let mut buf = vec![0u8; (stride * h * 3 / 2) as usize];
            let mut times = Vec::new();
            let mut bytes = 0usize;
            let mut skipped = 0;
            let cpu0 = stats::process_cpu_seconds();
            let t0 = Instant::now();
            for i in 0..frames {
                synth_frame(&mut buf, w, h, stride, i, full);
                let pic = encode::I420 { data: &buf, width: w, height: h, stride };
                let o = e.encode(&pic, i64::from(i) * 11)?;
                if o.skipped {
                    skipped += 1;
                }
                bytes += o.data.len();
                times.push(o.encode_us);
            }
            let wall = t0.elapsed().as_secs_f64();
            let cpu = stats::process_cpu_seconds() - cpu0;
            out.push(json!({
                "content": if full { "full-motion" } else { "ui-partial" }, "threads": t, "frames": frames,
                "encode_ms_p50": stats::percentile_u64(&times, 50.0) / 1000.0, "encode_ms_p95": stats::percentile_u64(&times, 95.0) / 1000.0,
                "max_fps": 1000.0 / (stats::percentile_u64(&times, 50.0) / 1000.0).max(0.01),
                "kbit_per_frame": bytes as f64 * 8.0 / 1000.0 / f64::from(frames), "skipped": skipped,
                "cpu_cores_while_encoding": cpu / wall,
            }));
        }
    }
    println!("{}", serde_json::to_string_pretty(&out)?);
    Ok(())
}

fn bench_capture(output: &str, secs: u64, hz: u32, with_encode: bool, bitmap: bool) -> Result<()> {
    if bitmap && with_encode { bail!("--bitmap and --encode are mutually exclusive"); }
    let stop = Arc::new(AtomicBool::new(false));
    let mut cap = capture::spawn(capture::CaptureParams {
        output: output.to_owned(),
        paint_cursor: true,
        max_hz: Arc::new(AtomicU32::new(hz)),
        layout: Arc::new(AtomicU32::new(u32::from(bitmap))),
        paused: Arc::new(AtomicBool::new(false)),
        stop: Arc::clone(&stop),
        matrix: gpu::Matrix::Bt709Full,
        modifiers: "auto".into(),
        path: std::env::var("MIOS_RDP_CAPTURE_PATH").unwrap_or_else(|_| "auto".into()),
        convert_threads: std::env::var("MIOS_RDP_CONVERT_THREADS").ok().and_then(|t| t.parse().ok()).unwrap_or(4),
        gpu_priority: std::env::var("MIOS_RDP_GPU_PRIORITY").unwrap_or_else(|_| "low".into()),
        output_wait: Duration::from_secs(5),
    })?;
    let t0 = Instant::now();
    let mut conv = Vec::new();
    let mut capus = Vec::new();
    let mut n = 0u32;
    let mut enc: Option<encode::OpenH264> = None;
    let mut enc_us = Vec::new();
    let cpu0 = stats::process_cpu_seconds();
    let mut reference: Option<capture::Frame> = None;
    let mut bitmap_samples = Vec::new();
    let mut bitmap_first_bytes = None;
    while t0.elapsed() < Duration::from_secs(secs) {
        match cap.mailbox.take(Duration::from_millis(200)) {
            Ok(Some(mut f)) => {
                n += 1;
                conv.push(f.convert_us);
                capus.push(f.capture_us);
                if bitmap {
                    let encode_rect = |data: &[u8], x: usize, y: usize, w: usize, h: usize| {
                        ironrdp_nscodec::encoder::encode(&data[y*f.stride as usize+x*4..], w as u16, h as u16, f.stride as usize, ironrdp_server::PixelFormat::BgrA32, 3).len()
                    };
                    if let Some(previous) = reference.as_ref().filter(|p| p.width == f.width && p.height == f.height && p.stride == f.stride) {
                        let w = f.width as usize;
                        let h = f.height as usize;
                        let stride = f.stride as usize;
                        let dirty = |data: &[u8]| -> usize {
                            (0..h).map(|y| (0..w).filter(|&x| {
                                let i = y * stride + x * 4;
                                data[i..i+4] != previous.data[i..i+4]
                            }).count()).sum()
                        };
                        let before = dirty(&f.data);
                        pipeline::stabilize_bitmap(&mut f.data, &previous.data, w, h, stride, config::Native::default().bitmap_damage_threshold);
                        let after = dirty(&f.data);
                        let rects = ironrdp_graphics::diff::find_different_rects_sub::<4>(&previous.data, stride, w, h, &f.data, stride, w, h, 0, 0);
                        let encoded_bytes: usize=rects.iter().map(|r|encode_rect(&f.data,r.x,r.y,r.width,r.height)).sum();
                        bitmap_samples.push(json!({"changed_before":before,"changed_after":after,"pixels":w*h,"rectangles":rects.len(),"tile_pixels":rects.iter().map(|r|r.width*r.height).sum::<usize>(),"nscodec_bytes":encoded_bytes}));
                    } else {
                        bitmap_first_bytes=Some(encode_rect(&f.data,0,0,f.width as usize,f.height as usize));
                    }
                    if let Some(previous) = reference.replace(f) { cap.mailbox.recycle(previous.data); }
                    continue;
                }
                if with_encode {
                    use encode::H264Encoder as _;
                    let e = enc.get_or_insert_with(|| {
                        encode::OpenH264::new(
                            "auto",
                            encode::EncoderSettings {
                                width: f.width,
                                height: f.height,
                                fps: hz as f32,
                                bitrate_bps: 8_000_000,
                                max_bitrate_bps: 25_000_000,
                                qp_min: 18,
                                qp_max: 40,
                                threads: 4,
                                complexity: "low".into(),
                                full_range: true,
                            },
                        )
                        .expect("encoder")
                    });
                    let pic = encode::I420 { data: &f.data, width: f.width, height: f.height, stride: f.stride };
                    if let Ok(o) = e.encode(&pic, t0.elapsed().as_millis() as i64) {
                        enc_us.push(o.encode_us);
                    }
                }
                cap.mailbox.recycle(f.data);
            }
            Ok(None) => {}
            Err(e) => bail!("capture ended: {e}"),
        }
    }
    let wall = t0.elapsed().as_secs_f64();
    let cpu = stats::process_cpu_seconds() - cpu0;
    let info = cap.info.lock().expect("info").clone();
    stop.store(true, Ordering::SeqCst);
    cap.stop();
    println!(
        "{}",
        serde_json::to_string_pretty(&json!({
            "output": output, "seconds": wall, "frames": n, "fps": f64::from(n) / wall,
            "convert_ms_p50": stats::percentile_u64(&conv, 50.0) / 1000.0, "convert_ms_p95": stats::percentile_u64(&conv, 95.0) / 1000.0,
            "capture_wait_ms_p50": stats::percentile_u64(&capus, 50.0) / 1000.0,
            "encode_ms_p50": stats::percentile_u64(&enc_us, 50.0) / 1000.0, "encode_ms_p95": stats::percentile_u64(&enc_us, 95.0) / 1000.0,
            "cpu_cores": cpu / wall, "info": info, "bitmap_samples": bitmap_samples,
            "fps_basis": "capture", "bitmap_first_nscodec_bytes": bitmap_first_bytes,
            "bitmap_probe_color_loss_level": if bitmap { Some(3) } else { None },
        }))?
    );
    Ok(())
}
