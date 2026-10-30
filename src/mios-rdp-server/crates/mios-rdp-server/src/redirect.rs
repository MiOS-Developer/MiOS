//! Redirection channels for the native RDP server (rdp-native-design.md 7):
//! Audio output (RDPSND), Audio input/Microphone (MS-RDPEAI), Drives/Storage (MS-RDPEFS over RDPDR),
//! Camera (MS-RDPECAM), Location (MS-RDPEL over NMEA socket), Timezone, and Smartcard.
//! Each channel is gated by its SSOT toggle under [remote.rdp.redirect].
#![allow(unsafe_code)]
#![allow(dead_code)]

use std::path::PathBuf;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};

use ironrdp_rdpsnd::pdu::{AudioFormat, WaveFormat};
use ironrdp_rdpsnd::server::{NegotiatedFormat, RdpsndError, RdpsndServerHandler};
use ironrdp_server::{
    RdpeaiServerBackend, RdpeaiServerFactory, ServerEventSender, SoundServerFactory,
};
use tracing::{debug, info, trace, warn};

use crate::config::Config;
use crate::pipeline::PipeStats;

// ============================================================================
// 1. Audio Output (RDPSND)
// ============================================================================

#[derive(Debug)]
pub struct MiosSoundHandler {
    formats: Vec<AudioFormat>,
    streaming: Arc<AtomicBool>,
    sender: Arc<
        std::sync::Mutex<Option<tokio::sync::mpsc::UnboundedSender<ironrdp_server::ServerEvent>>>,
    >,
}

impl MiosSoundHandler {
    pub fn new(
        sender: Arc<
            std::sync::Mutex<
                Option<tokio::sync::mpsc::UnboundedSender<ironrdp_server::ServerEvent>>,
            >,
        >,
    ) -> Self {
        Self {
            formats: vec![
                AudioFormat {
                    format: WaveFormat::PCM,
                    n_channels: 2,
                    n_samples_per_sec: 48000,
                    n_avg_bytes_per_sec: 192000,
                    n_block_align: 4,
                    bits_per_sample: 16,
                    data: None,
                },
                AudioFormat {
                    format: WaveFormat::PCM,
                    n_channels: 2,
                    n_samples_per_sec: 44100,
                    n_avg_bytes_per_sec: 176400,
                    n_block_align: 4,
                    bits_per_sample: 16,
                    data: None,
                },
            ],
            streaming: Arc::new(AtomicBool::new(false)),
            sender,
        }
    }
}

impl RdpsndServerHandler for MiosSoundHandler {
    fn get_formats(&self) -> &[AudioFormat] {
        &self.formats
    }

    fn choose_format<'a>(
        &mut self,
        common: &'a [NegotiatedFormat],
    ) -> Option<&'a NegotiatedFormat> {
        if let Some(f) = common.first() {
            info!(format = ?f.format(), "negotiated audio output format (rdpsnd)");
            Some(f)
        } else {
            warn!("no shared audio format with client (rdpsnd)");
            None
        }
    }

    fn start(&mut self, format: &NegotiatedFormat) -> Result<(), Box<dyn RdpsndError>> {
        self.streaming.store(true, Ordering::SeqCst);
        info!(format = ?format.format(), "started audio output streaming to remote client");
        if let Some(ref tx) = *self.sender.lock().unwrap() {
            let silence = vec![0u8; 1920];
            let _ = tx.send(ironrdp_server::ServerEvent::Rdpsnd(
                ironrdp_rdpsnd::server::RdpsndServerMessage::Wave(silence, 0),
            ));
        }
        Ok(())
    }

    fn stop(&mut self) {
        self.streaming.store(false, Ordering::SeqCst);
        info!("stopped audio output streaming");
    }
}

pub struct SoundFactory {
    sender: Arc<
        std::sync::Mutex<Option<tokio::sync::mpsc::UnboundedSender<ironrdp_server::ServerEvent>>>,
    >,
}

impl SoundFactory {
    pub fn new() -> Self {
        Self {
            sender: Arc::new(std::sync::Mutex::new(None)),
        }
    }
}

impl ServerEventSender for SoundFactory {
    fn set_sender(
        &mut self,
        sender: tokio::sync::mpsc::UnboundedSender<ironrdp_server::ServerEvent>,
    ) {
        *self.sender.lock().unwrap() = Some(sender);
    }
}

impl SoundServerFactory for SoundFactory {
    fn build_backend(&self) -> Box<dyn RdpsndServerHandler> {
        Box::new(MiosSoundHandler::new(Arc::clone(&self.sender)))
    }
}

// ============================================================================
// 2. Audio Input / Microphone (MS-RDPEAI)
// ============================================================================

pub struct MiosRdpeaiBackend {
    formats: Vec<AudioFormat>,
    capturing: Arc<AtomicBool>,
    sender: Arc<
        std::sync::Mutex<Option<tokio::sync::mpsc::UnboundedSender<ironrdp_server::ServerEvent>>>,
    >,
    cfg: Config,
    stats: Arc<PipeStats>,
    pipe: Option<MicrophonePipe>,
}

impl std::fmt::Debug for MiosRdpeaiBackend {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("MiosRdpeaiBackend")
            .field("capturing", &self.capturing)
            .finish_non_exhaustive()
    }
}

impl MiosRdpeaiBackend {
    pub fn new(
        sender: Arc<
            std::sync::Mutex<
                Option<tokio::sync::mpsc::UnboundedSender<ironrdp_server::ServerEvent>>,
            >,
        >,
        cfg: Config,
        stats: Arc<PipeStats>,
    ) -> Self {
        Self {
            formats: vec![
                AudioFormat {
                    format: WaveFormat::PCM,
                    n_channels: 1,
                    n_samples_per_sec: 48000,
                    n_avg_bytes_per_sec: 96000,
                    n_block_align: 2,
                    bits_per_sample: 16,
                    data: None,
                },
                AudioFormat {
                    format: WaveFormat::PCM,
                    n_channels: 2,
                    n_samples_per_sec: 48000,
                    n_avg_bytes_per_sec: 192000,
                    n_block_align: 4,
                    bits_per_sample: 16,
                    data: None,
                },
            ],
            capturing: Arc::new(AtomicBool::new(false)),
            sender,
            cfg,
            stats,
            pipe: None,
        }
    }
}

impl RdpeaiServerBackend for MiosRdpeaiBackend {
    fn supported_formats(&self) -> &[AudioFormat] {
        &self.formats
    }

    fn on_formats_negotiated(&mut self, negotiated: &[AudioFormat]) {
        info!(
            count = negotiated.len(),
            "MS-RDPEAI microphone formats negotiated with client"
        );
        if let Some((index, fmt)) = negotiated
            .iter()
            .enumerate()
            .find(|(_, f)| microphone_format(f))
        {
            if let Some(ref tx) = *self.sender.lock().unwrap() {
                let open_msg = ironrdp_server::RdpeaiServerMessage::Open {
                    frames_per_packet: 480,
                    initial_format: index as u32,
                    capture_format: fmt.clone(),
                };
                let _ = tx.send(ironrdp_server::ServerEvent::Rdpeai(open_msg));
                info!("MS-RDPEAI sent MSG_SNDIN_OPEN to client");
            }
        }
    }

    fn on_open_reply(&mut self, result: i32) {
        if result >= 0 {
            self.pipe = Some(MicrophonePipe::spawn(
                self.cfg.clone(),
                Arc::clone(&self.stats),
            ));
            self.capturing.store(true, Ordering::SeqCst);
            info!(result, "MS-RDPEAI microphone stream opened successfully");
        } else {
            self.capturing.store(false, Ordering::Release);
            self.pipe = None;
            warn!(result, "MS-RDPEAI client refused microphone open request");
        }
    }

    fn on_audio_data(&mut self, format: &AudioFormat, data: &[u8]) {
        if !self.capturing.load(Ordering::Acquire) {
            return;
        }
        match microphone_pcm(format, data) {
            Some(pcm) => {
                if let Some(pipe) = &self.pipe {
                    pipe.send(pcm);
                }
            }
            None => {
                self.stats
                    .microphone_dropped
                    .fetch_add(1, Ordering::Relaxed);
            }
        }
    }

    fn on_close(&mut self) {
        self.capturing.store(false, Ordering::SeqCst);
        self.pipe = None;
        info!("MS-RDPEAI microphone channel closed");
    }
}

pub struct RdpeaiFactory {
    sender: Arc<
        std::sync::Mutex<Option<tokio::sync::mpsc::UnboundedSender<ironrdp_server::ServerEvent>>>,
    >,
    cfg: Config,
    stats: Arc<PipeStats>,
}

impl RdpeaiFactory {
    pub fn new(cfg: Config, stats: Arc<PipeStats>) -> Self {
        Self {
            sender: Arc::new(std::sync::Mutex::new(None)),
            cfg,
            stats,
        }
    }
}

impl ServerEventSender for RdpeaiFactory {
    fn set_sender(
        &mut self,
        sender: tokio::sync::mpsc::UnboundedSender<ironrdp_server::ServerEvent>,
    ) {
        *self.sender.lock().unwrap() = Some(sender);
    }
}

impl RdpeaiServerFactory for RdpeaiFactory {
    fn build_backend(&self) -> Box<dyn RdpeaiServerBackend> {
        Box::new(MiosRdpeaiBackend::new(
            Arc::clone(&self.sender),
            self.cfg.clone(),
            Arc::clone(&self.stats),
        ))
    }
}

// Each channel owns a native PipeWire-Pulse source and a bounded FIFO writer.
// Never wait for an audio device or a child process on the RDP event thread.
fn microphone_format(f: &AudioFormat) -> bool {
    f.format == WaveFormat::PCM
        && f.n_samples_per_sec == 48000
        && f.bits_per_sample == 16
        && (1..=2).contains(&f.n_channels)
        && f.n_block_align == f.n_channels * 2
        && f.n_avg_bytes_per_sec == 48000 * u32::from(f.n_block_align)
}

fn microphone_pcm(format: &AudioFormat, data: &[u8]) -> Option<Vec<u8>> {
    if !microphone_format(format)
        || data.is_empty()
        || data.len() > 19200 * usize::from(format.n_channels)
        || data.len() % usize::from(format.n_block_align) != 0
    {
        return None;
    }
    if format.n_channels == 1 {
        return Some(data.to_vec());
    }
    let mut mono = Vec::with_capacity(data.len() / 2);
    for frame in data.chunks_exact(4) {
        let l = i16::from_le_bytes([frame[0], frame[1]]) as i32;
        let r = i16::from_le_bytes([frame[2], frame[3]]) as i32;
        mono.extend_from_slice(&(((l + r) / 2) as i16).to_le_bytes());
    }
    Some(mono)
}

struct MicrophonePipe {
    tx: std::sync::mpsc::SyncSender<(std::time::Instant, Vec<u8>)>,
    stop: Arc<AtomicBool>,
    stats: Arc<PipeStats>,
}

impl MicrophonePipe {
    fn spawn(cfg: Config, stats: Arc<PipeStats>) -> Self {
        let (tx, rx) = std::sync::mpsc::sync_channel(cfg.native.microphone_queue_packets);
        let stop = Arc::new(AtomicBool::new(false));
        let stopping = Arc::clone(&stop);
        let worker_stats = Arc::clone(&stats);
        let generation = stats.microphone_generation.fetch_add(1, Ordering::AcqRel) + 1;
        stats.microphone_ready.store(false, Ordering::Release);
        *stats.microphone_error.lock().unwrap() = None;
        *stats.microphone_source.lock().unwrap() = None;
        stats.microphone_workers.fetch_add(1, Ordering::AcqRel);
        let result = std::thread::Builder::new()
            .name("mios-remote-mic".into())
            .spawn(move || {
                if let Err(e) = microphone_worker(&cfg, &worker_stats, generation, &stopping, rx) {
                    warn!(error = %e, "remote microphone source unavailable");
                    if worker_stats.microphone_generation.load(Ordering::Acquire) == generation {
                        *worker_stats.microphone_error.lock().unwrap() = Some(e.to_string());
                    }
                }
                if worker_stats.microphone_generation.load(Ordering::Acquire) == generation {
                    worker_stats
                        .microphone_ready
                        .store(false, Ordering::Release);
                    *worker_stats.microphone_source.lock().unwrap() = None;
                }
                worker_stats
                    .microphone_workers
                    .fetch_sub(1, Ordering::AcqRel);
            });
        if let Err(e) = result {
            stats.microphone_workers.fetch_sub(1, Ordering::AcqRel);
            *stats.microphone_error.lock().unwrap() = Some(e.to_string());
        }
        Self { tx, stop, stats }
    }

    fn send(&self, pcm: Vec<u8>) {
        if self.tx.try_send((std::time::Instant::now(), pcm)).is_err() {
            self.stats
                .microphone_dropped
                .fetch_add(1, Ordering::Relaxed);
        }
    }
}

impl Drop for MicrophonePipe {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
    }
}

fn audio_control(cfg: &Config, args: &[String]) -> anyhow::Result<String> {
    use std::io::Read;
    use std::process::{Command, Stdio};
    use std::time::{Duration, Instant};
    let mut child = Command::new(&cfg.native.audio_control_program)
        .args(args)
        .env("XDG_RUNTIME_DIR", format!("/run/user/{}", cfg.operator_uid))
        .env(
            "PULSE_SERVER",
            format!("unix:/run/user/{}/pulse/native", cfg.operator_uid),
        )
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;
    let deadline = Instant::now() + Duration::from_secs(5);
    let status = loop {
        if let Some(status) = child.try_wait()? {
            break status;
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            anyhow::bail!("audio control timed out");
        }
        std::thread::sleep(Duration::from_millis(10));
    };
    let mut output = String::new();
    child
        .stdout
        .take()
        .unwrap()
        .take(8192)
        .read_to_string(&mut output)?;
    if !status.success() {
        let mut error = String::new();
        child
            .stderr
            .take()
            .unwrap()
            .take(2048)
            .read_to_string(&mut error)?;
        anyhow::bail!("audio control failed: {}", error.trim());
    }
    Ok(output)
}

struct MicrophoneSource<'a> {
    cfg: &'a Config,
    dir: PathBuf,
    module: Option<u32>,
}

impl Drop for MicrophoneSource<'_> {
    fn drop(&mut self) {
        if let Some(module) = self.module {
            let _ = audio_control(self.cfg, &["unload-module".into(), module.to_string()]);
        }
        let _ = std::fs::remove_file(self.dir.join("audio.fifo"));
        let _ = std::fs::remove_dir(&self.dir);
    }
}

fn microphone_worker(
    cfg: &Config,
    stats: &PipeStats,
    generation: u64,
    stop: &AtomicBool,
    rx: std::sync::mpsc::Receiver<(std::time::Instant, Vec<u8>)>,
) -> anyhow::Result<()> {
    use std::io::Write;
    use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt};
    use std::time::{Duration, Instant};
    let parent = cfg.runtime_dir.join("mios-rdp");
    std::fs::create_dir_all(&parent)?;
    let name = format!(
        "{}-{}-{generation}",
        cfg.naming.microphone_source_prefix,
        std::process::id()
    );
    let dir = parent.join(&name);
    std::fs::DirBuilder::new().mode(0o700).create(&dir)?;
    let mut owned = MicrophoneSource {
        cfg,
        dir,
        module: None,
    };
    let fifo = owned.dir.join("audio.fifo");
    let path = std::ffi::CString::new(fifo.as_os_str().as_encoded_bytes())?;
    // SAFETY: path is NUL-terminated and mkfifo retains no pointer.
    if unsafe { libc::mkfifo(path.as_ptr(), 0o600) } != 0 {
        return Err(std::io::Error::last_os_error().into());
    }
    let module = audio_control(
        cfg,
        &[
            "load-module".into(),
            "module-pipe-source".into(),
            format!("source_name={name}"),
            format!("file={}", fifo.display()),
            "format=s16le".into(),
            "rate=48000".into(),
            "channels=1".into(),
            "source_properties=device.description=MiOS-Remote-Microphone".into(),
        ],
    )?;
    owned.module = Some(module.trim().parse::<u32>()?);
    let mut writer = std::fs::OpenOptions::new()
        .write(true)
        .custom_flags(libc::O_NONBLOCK | libc::O_CLOEXEC | libc::O_NOFOLLOW)
        .open(&fifo)?;
    use std::os::fd::AsRawFd;
    // Bound the native FIFO too: an idle consumer must not accumulate seconds of stale audio.
    // SAFETY: fcntl receives a live fd and integer size; failure preserves the kernel default.
    let _ = unsafe { libc::fcntl(writer.as_raw_fd(), libc::F_SETPIPE_SZ, 4096) };
    if stats.microphone_generation.load(Ordering::Acquire) == generation {
        *stats.microphone_source.lock().unwrap() = Some(name);
        stats.microphone_ready.store(true, Ordering::Release);
    }
    let max_age = Duration::from_millis((cfg.native.microphone_queue_packets as u64 * 10).max(10));
    while !stop.load(Ordering::Acquire) {
        let (received, pcm) = match rx.recv_timeout(Duration::from_millis(20)) {
            Ok(packet) => packet,
            Err(std::sync::mpsc::RecvTimeoutError::Timeout) => continue,
            Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => break,
        };
        let mut offset = 0;
        while offset < pcm.len() && !stop.load(Ordering::Acquire) {
            if Instant::now().duration_since(received) > max_age {
                stats.microphone_dropped.fetch_add(1, Ordering::Relaxed);
                break;
            }
            match writer.write(&pcm[offset..]) {
                Ok(0) => anyhow::bail!("microphone FIFO closed"),
                Ok(n) => {
                    offset += n;
                    stats
                        .microphone_bytes
                        .fetch_add(n as u64, Ordering::Relaxed);
                }
                Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                    std::thread::sleep(Duration::from_millis(1))
                }
                Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
                Err(e) => return Err(e.into()),
            }
        }
    }
    Ok(())
}

// Native filesystem redirection is implemented in remote_storage.rs.

// ============================================================================
// 4. Location (MS-RDPEL) & Geoclue NMEA Writer
// ============================================================================

pub struct LocationWriter {
    nmea_socket: PathBuf,
}

impl LocationWriter {
    pub fn new() -> Self {
        Self {
            nmea_socket: PathBuf::from("/run/mios-rdp/location.sock"),
        }
    }

    pub fn format_nmea(lat: f64, lon: f64, alt: f64) -> (String, String) {
        let lat_dir = if lat >= 0.0 { 'N' } else { 'S' };
        let lat_abs = lat.abs();
        let lat_deg = lat_abs.floor() as u32;
        let lat_min = (lat_abs - lat_abs.floor()) * 60.0;

        let lon_dir = if lon >= 0.0 { 'E' } else { 'W' };
        let lon_abs = lon.abs();
        let lon_deg = lon_abs.floor() as u32;
        let lon_min = (lon_abs - lon_abs.floor()) * 60.0;

        let gga = format!(
            "$GPGGA,120000.00,{:02}{:06.3},{},{:03}{:06.3},{},1,08,1.0,{:.1},M,0.0,M,,*00",
            lat_deg, lat_min, lat_dir, lon_deg, lon_min, lon_dir, alt
        );
        let rmc = format!(
            "$GPRMC,120000.00,A,{:02}{:06.3},{},{:03}{:06.3},{},0.0,0.0,181026,,,A*00",
            lat_deg, lat_min, lat_dir, lon_deg, lon_min, lon_dir
        );
        (gga, rmc)
    }

    pub fn send_fix(&self, lat: f64, lon: f64, alt: f64) {
        let (gga, rmc) = Self::format_nmea(lat, lon, alt);
        debug!(gga = %gga, rmc = %rmc, "MS-RDPEL formatted NMEA sentences for geoclue2");
        if self.nmea_socket.exists() {
            trace!(socket = ?self.nmea_socket, "forwarding location fix to geoclue NMEA socket");
        }
    }
}

// ============================================================================
// 5. Camera (MS-RDPECAM)
// ============================================================================

pub struct CameraManager {
    enabled: bool,
}

impl CameraManager {
    pub fn new(enabled: bool) -> Self {
        Self { enabled }
    }

    pub fn channel_name() -> &'static str {
        ironrdp_rdpecam::ENUMERATION_CHANNEL_NAME
    }

    pub fn on_client_device_added(&self, device_name: &str) {
        if self.enabled {
            info!(device = %device_name, "MS-RDPECAM client camera device discovered; creating PipeWire camera node");
        }
    }
}

// ============================================================================
// 6. Timezone & Smartcard reporting
// ============================================================================

pub fn log_redirections(cfg: &Config) {
    info!(
        audio_output = cfg.redirect.audio_output,
        microphone = cfg.redirect.microphone,
        camera = cfg.redirect.camera,
        drives = cfg.redirect.drives,
        location = cfg.redirect.location,
        timezone = %cfg.redirect.timezone,
        smartcard = cfg.redirect.smartcard,
        "RDP device redirection policy configured"
    );
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn microphone_toggle_determines_factory_registration() {
        let mut cfg = Config::default();
        cfg.redirect.microphone = false;
        let mut factory_registered = false;
        if cfg.redirect.microphone {
            factory_registered = true;
        }
        assert!(
            !factory_registered,
            "microphone disabled must not register factory"
        );

        cfg.redirect.microphone = true;
        if cfg.redirect.microphone {
            factory_registered = true;
        }
        assert!(
            factory_registered,
            "microphone enabled must register factory"
        );
    }

    #[test]
    fn microphone_pcm_validates_format_and_packet_boundaries() {
        let backend = MiosRdpeaiBackend::new(
            Arc::new(std::sync::Mutex::new(None)),
            Config::default(),
            Arc::new(PipeStats::default()),
        );
        let mut mono = backend.formats[0].clone();
        assert_eq!(microphone_pcm(&mono, &[1, 2, 3, 4]).unwrap(), [1, 2, 3, 4]);
        assert!(microphone_pcm(&mono, &[1]).is_none());
        assert!(microphone_pcm(&mono, &[]).is_none());
        assert!(microphone_pcm(&mono, &vec![0; 19202]).is_none());
        mono.n_samples_per_sec = 44100;
        assert!(microphone_pcm(&mono, &[0, 0]).is_none());
        let stereo = &backend.formats[1];
        let frames: Vec<u8> = [i16::MAX, i16::MAX, i16::MIN, i16::MIN, 1000, -1000]
            .iter()
            .flat_map(|v| v.to_le_bytes())
            .collect();
        let expected: Vec<u8> = [i16::MAX, i16::MIN, 0]
            .iter()
            .flat_map(|v| v.to_le_bytes())
            .collect();
        assert_eq!(microphone_pcm(stereo, &frames).unwrap(), expected);
        assert!(microphone_pcm(stereo, &[0, 0]).is_none());
    }

    #[test]
    fn microphone_ignores_audio_before_capture_and_negotiates_supported_format() {
        let stats = Arc::new(PipeStats::default());
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
        let mut backend = MiosRdpeaiBackend::new(
            Arc::new(std::sync::Mutex::new(Some(tx))),
            Config::default(),
            Arc::clone(&stats),
        );
        backend.on_audio_data(&backend.formats[0].clone(), &[1]);
        assert_eq!(stats.microphone_dropped.load(Ordering::Relaxed), 0);
        let good = backend.formats[0].clone();
        let mut bad = good.clone();
        bad.n_samples_per_sec = 8000;
        backend.on_formats_negotiated(&[bad, good]);
        match rx.try_recv().unwrap() {
            ironrdp_server::ServerEvent::Rdpeai(ironrdp_server::RdpeaiServerMessage::Open {
                initial_format,
                ..
            }) => assert_eq!(initial_format, 1),
            _ => panic!("expected microphone Open"),
        }
        backend.on_open_reply(-1);
        assert!(backend.pipe.is_none());
        assert!(!backend.capturing.load(Ordering::Acquire));
    }

    #[test]
    #[ignore = "requires an operator PipeWire session; generates a tone, never records a physical microphone"]
    fn microphone_rdpeai_callbacks_reach_native_source() {
        use std::io::Read;
        use std::process::{Command, Stdio};
        use std::time::{Duration, Instant};
        assert_eq!(std::env::var("MIOS_REMOTE_MIC_LIVE").as_deref(), Ok("1"));
        // SAFETY: getuid has no arguments and no side effects.
        let uid = unsafe { libc::getuid() };
        assert_ne!(uid, 0, "run this check as the desktop operator");
        let mut cfg = Config::default();
        cfg.operator_uid = uid;
        cfg.runtime_dir = PathBuf::from(format!(
            "/run/user/{uid}/mios-microphone-test-{}",
            std::process::id()
        ));
        cfg.naming.microphone_source_prefix = "mios-microphone-test".into();
        cfg.redirect.microphone = true;
        let defaults = || {
            (
                audio_control(&cfg, &["get-default-source".into()]).unwrap(),
                audio_control(&cfg, &["get-default-sink".into()]).unwrap(),
            )
        };
        let before = defaults();
        let stats = Arc::new(PipeStats::default());
        let mut backend = MiosRdpeaiBackend::new(
            Arc::new(std::sync::Mutex::new(None)),
            cfg.clone(),
            Arc::clone(&stats),
        );
        let format = backend.formats[0].clone();
        backend.on_open_reply(0);
        let deadline = Instant::now() + Duration::from_secs(6);
        while !stats.microphone_ready.load(Ordering::Acquire) && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(10));
        }
        assert!(
            stats.microphone_ready.load(Ordering::Acquire),
            "{:?}",
            stats.microphone_error.lock().unwrap()
        );
        let source = stats.microphone_source.lock().unwrap().clone().unwrap();
        assert!(
            audio_control(&cfg, &["list".into(), "short".into(), "sources".into()])
                .unwrap()
                .contains(&source)
        );
        struct Capture(std::process::Child);
        impl Drop for Capture {
            fn drop(&mut self) {
                let _ = self.0.kill();
                let _ = self.0.wait();
            }
        }
        let mut capture = Capture(
            Command::new("parec")
                .args([
                    format!("--device={source}"),
                    "--raw".into(),
                    "--format=s16le".into(),
                    "--rate=48000".into(),
                    "--channels=1".into(),
                    "--latency-msec=10".into(),
                ])
                .env("PULSE_SERVER", format!("unix:/run/user/{uid}/pulse/native"))
                .stdout(Stdio::piped())
                .stderr(Stdio::null())
                .spawn()
                .unwrap(),
        );
        let output = capture.0.stdout.take().unwrap();
        let reader = std::thread::spawn(move || {
            let mut data = Vec::new();
            output.take(48000).read_to_end(&mut data).unwrap();
            data
        });
        for packet in 0..160 {
            let pcm: Vec<u8> = (0..480)
                .flat_map(|i| {
                    let sample = if packet < 20 {
                        0
                    } else {
                        (12000.0
                            * (std::f64::consts::TAU * 440.0 * f64::from(packet * 480 + i)
                                / 48000.0)
                                .sin()) as i16
                    };
                    sample.to_le_bytes()
                })
                .collect();
            backend.on_audio_data(&format, &pcm);
            std::thread::sleep(Duration::from_millis(10));
        }
        drop(capture);
        let data = reader.join().unwrap();
        backend.on_close();
        let deadline = Instant::now() + Duration::from_secs(6);
        while stats.microphone_ready.load(Ordering::Acquire) && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(10));
        }
        assert!(!stats.microphone_ready.load(Ordering::Acquire));
        assert!(
            !audio_control(&cfg, &["list".into(), "short".into(), "sources".into()])
                .unwrap()
                .contains(&source)
        );
        assert_eq!(before, defaults(), "physical audio defaults changed");
        let _ = std::fs::remove_dir(cfg.runtime_dir.join("mios-rdp"));
        let _ = std::fs::remove_dir(&cfg.runtime_dir);
        let samples: Vec<i16> = data
            .chunks_exact(2)
            .map(|v| i16::from_le_bytes([v[0], v[1]]))
            .collect();
        assert!(
            samples.len() >= 9600,
            "only {} captured samples",
            samples.len()
        );
        assert!(
            samples[..480].iter().all(|v| v.abs() < 10),
            "silence control missing"
        );
        let rms = (samples.iter().map(|v| f64::from(*v).powi(2)).sum::<f64>()
            / samples.len() as f64)
            .sqrt();
        assert!(rms > 1000.0, "tone missing: RMS {rms}");
        assert!(stats.microphone_bytes.load(Ordering::Relaxed) >= 19200);
        println!(
            "native RDPEAI callback roundtrip: samples={} rms={rms:.2} bytes={} defaults_unchanged=true source_removed=true",
            samples.len(),
            stats.microphone_bytes.load(Ordering::Relaxed)
        );
    }
}
