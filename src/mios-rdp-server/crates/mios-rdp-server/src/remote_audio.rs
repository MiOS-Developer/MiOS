// AI-hint: Session-owned native audio for RDP: per-connection MS-RDPEA speaker sinks (PipeWire-Pulse pipe-sink -> negotiated PCM RDPSND waves, latency-gated by client confirms, silence-idle, cleanup on disconnect) and the shared ownership-checked pipe module, audio control and audio-server restart recovery the microphone also uses.
// AI-related: redirect.rs, config.rs, pipeline.rs, main.rs, usr/share/mios/mios.toml, tests/fixtures/mios-rdp/speaker.py, usr/share/doc/mios/remote-audio.md
// AI-functions: SoundFactory, SpeakerHandler, StreamClock, OwnedPipe, Recovery, audio_control, module_owned, pipe_writer_lost, speaker_formats
#![allow(unsafe_code)]

use std::os::fd::AsRawFd;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use ironrdp_rdpsnd::pdu::{AudioFormat, WaveFormat};
use ironrdp_rdpsnd::server::{NegotiatedFormat, RdpsndError, RdpsndServerHandler, RdpsndServerMessage};
use ironrdp_server::{ServerEvent, ServerEventSender, SoundServerFactory};
use tracing::{info, warn};

use crate::config::Config;
use crate::pipeline::PipeStats;

pub(crate) type EventSender = tokio::sync::mpsc::UnboundedSender<ServerEvent>;
pub(crate) type SharedSender = Arc<Mutex<Option<EventSender>>>;

// ============================================================================
// Audio control and owned pipe modules (speaker sink and microphone source)
// ============================================================================

/// Runs the configured PipeWire-Pulse control program without a shell. Never
/// called on the RDP event thread: it can wait up to five seconds.
pub(crate) fn audio_control(cfg: &Config, args: &[String]) -> anyhow::Result<String> {
    use std::io::Read;
    use std::process::{Command, Stdio};
    let mut command = Command::new(&cfg.native.audio_control_program);
    command.args(args).env("PULSE_SERVER", cfg.pulse_server());
    if cfg.native.audio_server.is_empty() {
        command.env("XDG_RUNTIME_DIR", format!("/run/user/{}", cfg.operator_uid));
    }
    let mut child = command
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
    child.stdout.take().unwrap().take(8192).read_to_string(&mut output)?;
    if !status.success() {
        let mut error = String::new();
        child.stderr.take().unwrap().take(2048).read_to_string(&mut error)?;
        anyhow::bail!("audio control failed: {}", error.trim());
    }
    Ok(output)
}

/// True only when `module` is still the exact pipe module of `kind` this
/// process loaded on `fifo`. Module IDs are reused after the audio daemon
/// restarts, so an ID alone never proves ownership.
pub(crate) fn module_owned(modules: &str, module: u32, kind: &str, fifo: &std::path::Path) -> bool {
    let id = module.to_string();
    let file = format!("file={}", fifo.display());
    modules.lines().any(|line| {
        let mut fields = line.split('\t');
        fields.next() == Some(id.as_str())
            && fields.next() == Some(kind)
            && fields.next().is_some_and(|args| args.split_ascii_whitespace().any(|arg| arg == file))
    })
}

/// A session-owned PipeWire-Pulse pipe module and its private FIFO under
/// `<runtime_dir>/mios-rdp/<name>/audio.fifo`. Dropping it unloads only the
/// module it verifiably owns and removes the FIFO and directory.
pub(crate) struct OwnedPipe<'a> {
    cfg: &'a Config,
    dir: PathBuf,
    kind: &'static str,
    module: Option<u32>,
}

impl<'a> OwnedPipe<'a> {
    pub(crate) fn create(cfg: &'a Config, name: &str, kind: &'static str, args: &[String]) -> anyhow::Result<Self> {
        use std::os::unix::fs::DirBuilderExt;
        let parent = cfg.runtime_dir.join("mios-rdp");
        std::fs::create_dir_all(&parent)?;
        let dir = parent.join(name);
        // A recovered node keeps its name; clear only this node's own leftovers.
        let _ = std::fs::remove_file(dir.join("audio.fifo"));
        let _ = std::fs::remove_dir(&dir);
        std::fs::DirBuilder::new().mode(0o700).create(&dir)?;
        let mut owned = Self { cfg, dir, kind, module: None };
        let fifo = owned.fifo();
        let path = std::ffi::CString::new(fifo.as_os_str().as_encoded_bytes())?;
        // SAFETY: path is NUL-terminated and mkfifo retains no pointer.
        if unsafe { libc::mkfifo(path.as_ptr(), 0o600) } != 0 {
            return Err(std::io::Error::last_os_error().into());
        }
        let mut argv = vec!["load-module".to_owned(), kind.to_owned()];
        argv.extend(args.iter().cloned());
        argv.push(format!("file={}", fifo.display()));
        let module = audio_control(cfg, &argv)?;
        owned.module = Some(module.trim().parse::<u32>()?);
        Ok(owned)
    }

    pub(crate) fn fifo(&self) -> PathBuf {
        self.dir.join("audio.fifo")
    }
}

impl Drop for OwnedPipe<'_> {
    fn drop(&mut self) {
        if let Some(module) = self.module {
            let fifo = self.fifo();
            if audio_control(self.cfg, &["list".into(), "short".into(), "modules".into()])
                .is_ok_and(|modules| module_owned(&modules, module, self.kind, &fifo))
            {
                let _ = audio_control(self.cfg, &["unload-module".into(), module.to_string()]);
            }
        }
        let _ = std::fs::remove_file(self.fifo());
        let _ = std::fs::remove_dir(&self.dir);
    }
}

/// Bounded audio-server restart recovery: one budget of attempts per outage,
/// `audio_recovery_interval_ms` apart; zero attempts disables recovery.
pub(crate) struct Recovery {
    left: u32,
    budget: u32,
    interval: Duration,
}

impl Recovery {
    pub(crate) fn new(cfg: &Config) -> Self {
        Self {
            left: cfg.native.audio_recovery_attempts,
            budget: cfg.native.audio_recovery_attempts,
            interval: Duration::from_millis(cfg.native.audio_recovery_interval_ms),
        }
    }

    pub(crate) fn reset(&mut self) {
        self.left = self.budget;
    }

    /// Waits one interval before the next attempt. False when the budget is
    /// spent, recovery is disabled or the connection is closing.
    pub(crate) fn wait(&mut self, stop: &AtomicBool) -> bool {
        if self.left == 0 || stop.load(Ordering::Acquire) {
            return false;
        }
        self.left -= 1;
        let deadline = Instant::now() + self.interval;
        while let Some(left) = deadline.checked_duration_since(Instant::now()) {
            if stop.load(Ordering::Acquire) {
                return false;
            }
            std::thread::sleep(left.min(Duration::from_millis(20)));
        }
        !stop.load(Ordering::Acquire)
    }
}

/// The audio server holds a FIFO it reads from open read-write; when it
/// exits or unloads the module, the write end reports POLLERR. Checked
/// without blocking so an idle microphone notices a restart too.
pub(crate) fn pipe_writer_lost(fd: &impl AsRawFd) -> bool {
    let mut pfd = libc::pollfd { fd: fd.as_raw_fd(), events: libc::POLLOUT, revents: 0 };
    // SAFETY: one valid pollfd for a live descriptor, zero timeout.
    let ready = unsafe { libc::poll(&mut pfd, 1, 0) };
    ready > 0 && pfd.revents & (libc::POLLERR | libc::POLLNVAL) != 0
}

// ============================================================================
// Speaker: MS-RDPEA (RDPSND) server-to-client audio
// ============================================================================

/// PCM formats offered to the client, in preference order. The session sink
/// is created at the negotiated rate and channel count, so PipeWire resamples
/// applications into it and the waves need no conversion.
pub(crate) fn speaker_formats() -> Vec<AudioFormat> {
    [(2u16, 48000u32), (2, 44100), (1, 48000), (1, 44100), (2, 22050)]
        .into_iter()
        .map(|(channels, rate)| AudioFormat {
            format: WaveFormat::PCM,
            n_channels: channels,
            n_samples_per_sec: rate,
            n_avg_bytes_per_sec: rate * u32::from(channels) * 2,
            n_block_align: channels * 2,
            bits_per_sample: 16,
            data: None,
        })
        .collect()
}

fn speaker_format_supported(format: &AudioFormat) -> bool {
    speaker_formats().iter().any(|f| f == format)
}

fn format_label(format: &AudioFormat) -> String {
    format!("pcm s16le {}Hz {}ch", format.n_samples_per_sec, format.n_channels)
}

/// Stream clock shared by the RDP event thread (Wave Confirm PDUs) and the
/// sink reader. Times are milliseconds since the stream started; waves carry
/// the same value, so a confirm's 16-bit timestamp maps back onto it.
#[derive(Debug)]
pub(crate) struct StreamClock {
    epoch: Instant,
    started: AtomicBool,
    first_sent: AtomicU32,
    last_sent: AtomicU32,
    confirmed: AtomicU32,
    confirm_seen: AtomicBool,
    last_probe: AtomicU32,
    probe_interval: AtomicU32,
}

/// Longest wait between probe waves to a client that stopped confirming.
const PROBE_INTERVAL_MAX_MS: u32 = 8000;

impl StreamClock {
    pub(crate) fn new() -> Self {
        Self {
            epoch: Instant::now(),
            started: AtomicBool::new(false),
            first_sent: AtomicU32::new(0),
            last_sent: AtomicU32::new(0),
            confirmed: AtomicU32::new(0),
            confirm_seen: AtomicBool::new(false),
            last_probe: AtomicU32::new(0),
            probe_interval: AtomicU32::new(0),
        }
    }

    pub(crate) fn now_ms(&self) -> u32 {
        u32::try_from(self.epoch.elapsed().as_millis() & u128::from(u32::MAX)).unwrap_or(0)
    }

    pub(crate) fn sent(&self, ts: u32) {
        if !self.started.swap(true, Ordering::AcqRel) {
            self.first_sent.store(ts, Ordering::Release);
        }
        self.last_sent.store(ts, Ordering::Release);
    }

    /// Records a Wave Confirm. MS-RDPEA sets its timestamp to the wave's own
    /// plus the time the client held it, so the newest confirm is a lower
    /// bound of what the client has received. Any confirm ends probing.
    pub(crate) fn confirm(&self, timestamp: u16) {
        let last = self.last_sent.load(Ordering::Acquire);
        let delta = i32::from(timestamp.wrapping_sub(last as u16) as i16);
        let value = last.wrapping_add_signed(delta);
        let seen = self.confirm_seen.load(Ordering::Acquire);
        let previous = self.confirmed.load(Ordering::Acquire);
        if !seen || (value.wrapping_sub(previous) as i32) > 0 {
            self.confirmed.store(value, Ordering::Release);
        }
        self.probe_interval.store(0, Ordering::Release);
        self.confirm_seen.store(true, Ordering::Release);
    }

    /// Audio sent but not yet confirmed, in milliseconds; None before the
    /// client confirmed anything.
    pub(crate) fn backlog_ms(&self) -> Option<u32> {
        if !self.confirm_seen.load(Ordering::Acquire) {
            return None;
        }
        let backlog = self.last_sent.load(Ordering::Acquire).wrapping_sub(self.confirmed.load(Ordering::Acquire)) as i32;
        Some(backlog.max(0) as u32)
    }

    /// Latency gate that keeps the outgoing queue bounded. A wave is admitted
    /// while at most `max_ms` of sent audio is unconfirmed; before the first
    /// confirm the client gets a start-up window of `max(4 * max_ms, 1000)`.
    /// Past that the client is behind or stalled: only a probe wave is
    /// admitted, first after one window and then with a doubling interval up
    /// to 8 s, so a recovered client confirms and resumes while a stalled one
    /// queues almost nothing.
    pub(crate) fn admit(&self, now: u32, max_ms: u32) -> bool {
        if !self.started.load(Ordering::Acquire) {
            return true;
        }
        let startup = max_ms.saturating_mul(4).max(1000);
        let (reference, window) = if self.confirm_seen.load(Ordering::Acquire) {
            (self.confirmed.load(Ordering::Acquire), max_ms)
        } else {
            (self.first_sent.load(Ordering::Acquire), startup)
        };
        let backlog = (self.last_sent.load(Ordering::Acquire).wrapping_sub(reference) as i32).max(0) as u32;
        if backlog <= window {
            self.probe_interval.store(0, Ordering::Release);
            return true;
        }
        let interval = self.probe_interval.load(Ordering::Acquire);
        if interval == 0 {
            // Just fell behind: the next probe waits one start-up window.
            self.probe_interval.store(startup, Ordering::Release);
            self.last_probe.store(now, Ordering::Release);
            return false;
        }
        if now.wrapping_sub(self.last_probe.load(Ordering::Acquire)) < interval {
            return false;
        }
        self.last_probe.store(now, Ordering::Release);
        self.probe_interval.store(interval.saturating_mul(2).min(PROBE_INTERVAL_MAX_MS), Ordering::Release);
        true
    }
}

pub struct SoundFactory {
    sender: SharedSender,
    cfg: Config,
    stats: Arc<PipeStats>,
}

impl SoundFactory {
    pub fn new(cfg: Config, stats: Arc<PipeStats>) -> Self {
        Self { sender: Arc::new(Mutex::new(None)), cfg, stats }
    }
}

impl ServerEventSender for SoundFactory {
    fn set_sender(&mut self, sender: EventSender) {
        *self.sender.lock().unwrap() = Some(sender);
    }
}

impl SoundServerFactory for SoundFactory {
    fn build_backend(&self) -> Box<dyn RdpsndServerHandler> {
        Box::new(SpeakerHandler::new(Arc::clone(&self.sender), self.cfg.clone(), Arc::clone(&self.stats)))
    }
}

struct SpeakerStream {
    stop: Arc<AtomicBool>,
    clock: Arc<StreamClock>,
}

/// One per RDP connection: the RDPSND channel's backend. The sink reader runs
/// on its own thread; nothing here waits for the audio server.
pub struct SpeakerHandler {
    formats: Vec<AudioFormat>,
    sender: SharedSender,
    cfg: Config,
    stats: Arc<PipeStats>,
    stream: Option<SpeakerStream>,
}

impl std::fmt::Debug for SpeakerHandler {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("SpeakerHandler").field("streaming", &self.stream.is_some()).finish_non_exhaustive()
    }
}

impl SpeakerHandler {
    pub(crate) fn new(sender: SharedSender, cfg: Config, stats: Arc<PipeStats>) -> Self {
        Self { formats: speaker_formats(), sender, cfg, stats, stream: None }
    }

    fn halt(&mut self) {
        if let Some(stream) = self.stream.take() {
            stream.stop.store(true, Ordering::Release);
        }
    }
}

fn rdpsnd_error(message: &str) -> Box<dyn RdpsndError> {
    Box::new(std::io::Error::other(message.to_owned()))
}

impl RdpsndServerHandler for SpeakerHandler {
    fn get_formats(&self) -> &[AudioFormat] {
        &self.formats
    }

    fn choose_format<'a>(&mut self, common: &'a [NegotiatedFormat]) -> Option<&'a NegotiatedFormat> {
        let chosen = common.iter().find(|f| speaker_format_supported(f.format()));
        match chosen {
            Some(f) => info!(format = %format_label(f.format()), common = common.len(), "negotiated speaker format (rdpsnd)"),
            None => warn!("no shared PCM speaker format with the client (rdpsnd)"),
        }
        chosen
    }

    fn start(&mut self, format: &NegotiatedFormat) -> Result<(), Box<dyn RdpsndError>> {
        let format = format.format().clone();
        if !speaker_format_supported(&format) {
            return Err(rdpsnd_error("unsupported speaker format"));
        }
        let Some(tx) = self.sender.lock().unwrap().clone() else {
            return Err(rdpsnd_error("no server event sender for speaker waves"));
        };
        self.halt();
        let stop = Arc::new(AtomicBool::new(false));
        let clock = Arc::new(StreamClock::new());
        spawn_speaker(self.cfg.clone(), Arc::clone(&self.stats), format, Arc::clone(&stop), Arc::clone(&clock), tx)
            .map_err(|e| rdpsnd_error(&e.to_string()))?;
        self.stream = Some(SpeakerStream { stop, clock });
        Ok(())
    }

    fn stop(&mut self) {
        if self.stream.is_some() {
            info!("speaker stream stopped; removing the session sink");
        }
        self.halt();
    }

    fn wave_confirm(&mut self, _block_no: u8, timestamp: u16) {
        if let Some(stream) = &self.stream {
            stream.clock.confirm(timestamp);
        }
    }
}

impl Drop for SpeakerHandler {
    fn drop(&mut self) {
        self.halt();
    }
}

fn spawn_speaker(
    cfg: Config,
    stats: Arc<PipeStats>,
    format: AudioFormat,
    stop: Arc<AtomicBool>,
    clock: Arc<StreamClock>,
    tx: EventSender,
) -> std::io::Result<()> {
    let generation = stats.speaker_generation.fetch_add(1, Ordering::AcqRel) + 1;
    stats.speaker_ready.store(false, Ordering::Release);
    *stats.speaker_error.lock().unwrap() = None;
    *stats.speaker_sink.lock().unwrap() = None;
    *stats.speaker_format.lock().unwrap() = Some(format_label(&format));
    stats.speaker_workers.fetch_add(1, Ordering::AcqRel);
    let worker_stats = Arc::clone(&stats);
    let result = std::thread::Builder::new().name("mios-remote-speaker".into()).spawn(move || {
        let current = |s: &PipeStats| s.speaker_generation.load(Ordering::Acquire) == generation;
        if let Err(e) = speaker_worker(&cfg, &worker_stats, generation, &stop, &clock, &format, &tx) {
            warn!(error = %e, "remote speaker sink unavailable");
            if current(&worker_stats) {
                *worker_stats.speaker_error.lock().unwrap() = Some(e.to_string());
            }
        }
        if current(&worker_stats) {
            worker_stats.speaker_ready.store(false, Ordering::Release);
            *worker_stats.speaker_sink.lock().unwrap() = None;
        }
        worker_stats.speaker_workers.fetch_sub(1, Ordering::AcqRel);
    });
    if let Err(e) = result {
        stats.speaker_workers.fetch_sub(1, Ordering::AcqRel);
        *stats.speaker_error.lock().unwrap() = Some(e.to_string());
        return Err(e);
    }
    Ok(())
}

/// Creates the sink and opens its FIFO for reading. Stale samples written
/// before the reader existed are discarded, and the pipe is kept small so a
/// late reader never accumulates more than a few packets.
fn open_speaker<'a>(cfg: &'a Config, name: &str, format: &AudioFormat, packet: usize) -> anyhow::Result<(OwnedPipe<'a>, std::fs::File)> {
    use std::io::Read;
    use std::os::unix::fs::OpenOptionsExt;
    let sink = OwnedPipe::create(
        cfg,
        name,
        "module-pipe-sink",
        &[
            format!("sink_name={name}"),
            "format=s16le".into(),
            format!("rate={}", format.n_samples_per_sec),
            format!("channels={}", format.n_channels),
            // Lowest session priority: WirePlumber never prefers this node over an
            // existing device when it picks a default, so routing stays the operator's.
            "sink_properties=\"device.description=MiOS-Remote-Speaker priority.session=0\"".into(),
        ],
    )?;
    let mut reader = std::fs::OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NONBLOCK | libc::O_CLOEXEC | libc::O_NOFOLLOW)
        .open(sink.fifo())?;
    let pipe_size = libc::c_int::try_from((packet * 2).max(16384)).unwrap_or(16384);
    // SAFETY: fcntl receives a live fd and integer size; failure keeps the kernel default.
    let _ = unsafe { libc::fcntl(reader.as_raw_fd(), libc::F_SETPIPE_SZ, pipe_size) };
    let mut scratch = [0u8; 16384];
    for _ in 0..64 {
        match reader.read(&mut scratch) {
            Ok(n) if n > 0 => continue,
            _ => break,
        }
    }
    Ok((sink, reader))
}

fn speaker_worker(
    cfg: &Config,
    stats: &PipeStats,
    generation: u64,
    stop: &AtomicBool,
    clock: &StreamClock,
    format: &AudioFormat,
    tx: &EventSender,
) -> anyhow::Result<()> {
    let name = format!("{}-{}-{generation}", cfg.naming.speaker_sink_prefix, std::process::id());
    let frames = (u64::from(format.n_samples_per_sec) * u64::from(cfg.native.speaker_packet_ms) / 1000) as usize;
    let packet = frames.max(1) * usize::from(format.n_block_align);
    let current = || stats.speaker_generation.load(Ordering::Acquire) == generation;
    let mut recovery = Recovery::new(cfg);
    let mut lost = false;
    while !stop.load(Ordering::Acquire) {
        let (sink, reader) = match open_speaker(cfg, &name, format, packet) {
            Ok(opened) => opened,
            Err(e) => {
                if current() {
                    *stats.speaker_error.lock().unwrap() = Some(e.to_string());
                }
                if recovery.wait(stop) {
                    continue;
                }
                return if stop.load(Ordering::Acquire) { Ok(()) } else { Err(e) };
            }
        };
        recovery.reset();
        if lost {
            stats.speaker_recoveries.fetch_add(1, Ordering::Relaxed);
            info!(sink = %name, "remote speaker sink recreated after the audio server restarted");
        }
        if current() {
            *stats.speaker_sink.lock().unwrap() = Some(name.clone());
            *stats.speaker_error.lock().unwrap() = None;
            stats.speaker_ready.store(true, Ordering::Release);
        }
        info!(sink = %name, format = %format_label(format), "remote speaker sink ready");
        match speaker_pump(cfg, stats, stop, clock, tx, &reader, packet) {
            Ok(()) => return Ok(()),
            Err(e) => {
                warn!(error = %e, sink = %name, "remote speaker sink lost");
                lost = true;
                if current() {
                    stats.speaker_ready.store(false, Ordering::Release);
                    *stats.speaker_error.lock().unwrap() = Some(format!("audio server lost: {e}"));
                }
                drop(reader);
                drop(sink);
                if !recovery.wait(stop) {
                    return if stop.load(Ordering::Acquire) { Ok(()) } else { Err(e) };
                }
            }
        }
    }
    Ok(())
}

/// Reads whole packets from the sink and sends them as waves. Ok means the
/// connection is closing; Err means the audio server dropped the sink.
fn speaker_pump(
    cfg: &Config,
    stats: &PipeStats,
    stop: &AtomicBool,
    clock: &StreamClock,
    tx: &EventSender,
    reader: &std::fs::File,
    packet: usize,
) -> anyhow::Result<()> {
    use std::io::Read;
    let packet_ms = cfg.native.speaker_packet_ms;
    let idle_ms = cfg.native.speaker_idle_ms;
    let max_ms = cfg.native.speaker_max_latency_ms;
    let mut reader = reader;
    let mut buf = vec![0u8; packet];
    let mut fill = 0usize;
    let mut silent_ms = 0u32;
    let mut gap = true;
    while !stop.load(Ordering::Acquire) {
        let mut pfd = libc::pollfd { fd: reader.as_raw_fd(), events: libc::POLLIN, revents: 0 };
        // SAFETY: one valid pollfd for a live descriptor; bounded timeout keeps stop responsive.
        let ready = unsafe { libc::poll(&mut pfd, 1, 50) };
        if ready < 0 {
            let e = std::io::Error::last_os_error();
            if e.kind() == std::io::ErrorKind::Interrupted {
                continue;
            }
            return Err(e.into());
        }
        if ready == 0 {
            // No samples for a poll period: the sink is suspended or starved.
            if !gap {
                stats.speaker_gaps.fetch_add(1, Ordering::Relaxed);
                gap = true;
            }
            if fill == 0 {
                continue;
            }
            // Flush the tail of the sound that just ended, padded with
            // silence, so it is neither lost nor played late.
            buf[fill..].fill(0);
            fill = packet;
        } else if pfd.revents & libc::POLLIN != 0 {
            match reader.read(&mut buf[fill..]) {
                Ok(0) => anyhow::bail!("speaker sink closed by the audio server"),
                Ok(n) => {
                    fill += n;
                    gap = false;
                }
                Err(e) if matches!(e.kind(), std::io::ErrorKind::WouldBlock | std::io::ErrorKind::Interrupted) => continue,
                Err(e) => return Err(e.into()),
            }
        } else if pfd.revents & (libc::POLLHUP | libc::POLLERR | libc::POLLNVAL) != 0 {
            anyhow::bail!("speaker sink closed by the audio server");
        }
        if fill < packet {
            continue;
        }
        fill = 0;
        silent_ms = if buf.iter().all(|b| *b == 0) { silent_ms.saturating_add(packet_ms) } else { 0 };
        if idle_ms > 0 && silent_ms > idle_ms {
            stats.speaker_idle_packets.fetch_add(1, Ordering::Relaxed);
            continue;
        }
        let now = clock.now_ms();
        if !clock.admit(now, max_ms) {
            stats.speaker_dropped.fetch_add(1, Ordering::Relaxed);
            continue;
        }
        if stop.load(Ordering::Acquire) {
            break;
        }
        if tx.send(ServerEvent::Rdpsnd(RdpsndServerMessage::Wave(buf.clone(), now))).is_err() {
            return Ok(());
        }
        clock.sent(now);
        stats.speaker_waves.fetch_add(1, Ordering::Relaxed);
        stats.speaker_bytes.fetch_add(packet as u64, Ordering::Relaxed);
        stats.speaker_backlog_ms.store(clock.backlog_ms().unwrap_or(0), Ordering::Relaxed);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pipe_cleanup_checks_module_kind_fifo_and_id() {
        let path = std::path::Path::new("/run/user/1000/mios-test/audio.fifo");
        let line = "42\tmodule-pipe-sink\tsink_name=s file=/run/user/1000/mios-test/audio.fifo rate=48000\t\n";
        assert!(module_owned(line, 42, "module-pipe-sink", path));
        assert!(!module_owned(line, 42, "module-pipe-source", path), "a sink is never unloaded as a source");
        assert!(!module_owned(line, 43, "module-pipe-sink", path));
        assert!(!module_owned("42\tmodule-pipe-sink\tfile=/run/user/1000/mios-test/audio.fifo-x\n", 42, "module-pipe-sink", path));
    }

    #[test]
    fn speaker_offers_only_consistent_pcm_and_prefers_48k_stereo() {
        let formats = speaker_formats();
        assert_eq!((formats[0].n_samples_per_sec, formats[0].n_channels), (48000, 2));
        for f in &formats {
            assert_eq!(f.format, WaveFormat::PCM);
            assert_eq!(f.bits_per_sample, 16);
            assert_eq!(f.n_block_align, f.n_channels * 2);
            assert_eq!(f.n_avg_bytes_per_sec, f.n_samples_per_sec * u32::from(f.n_block_align));
            assert!(speaker_format_supported(f));
        }
        let mut alaw = formats[0].clone();
        alaw.format = WaveFormat::ALAW;
        assert!(!speaker_format_supported(&alaw));
        let mut bad_rate = formats[0].clone();
        bad_rate.n_samples_per_sec = 8000;
        assert!(!speaker_format_supported(&bad_rate));
    }

    #[test]
    fn latency_gate_withholds_unconfirmed_audio_and_reopens_on_confirms() {
        let clock = StreamClock::new();
        // Before any wave the client is not gated.
        assert!(clock.admit(1000, 200));
        clock.sent(1000);
        // Confirm for the wave sent at 1000 (16-bit wire timestamp, held 3 ms).
        clock.confirm(1003);
        assert_eq!(clock.backlog_ms(), Some(0));
        // Client falls behind: 300 ms sent past the last confirm closes the gate.
        clock.sent(1303);
        assert_eq!(clock.backlog_ms(), Some(300));
        assert!(!clock.admit(1320, 200), "more than max latency unconfirmed must be withheld");
        // The backlog drains: confirms catch up and the gate reopens.
        clock.confirm(1250);
        assert!(clock.admit(1330, 200));
        // A stalled client is probed after one start-up window, then backed off.
        clock.sent(1600);
        assert!(!clock.admit(1600, 200));
        assert!(!clock.admit(2500, 200));
        assert!(clock.admit(2601, 200), "first probe after the start-up window");
        clock.sent(2601);
        assert!(!clock.admit(3601, 200), "probe interval doubles");
        assert!(clock.admit(4602, 200));
        clock.sent(4602);
        // The client answers the probe: normal flow resumes at once.
        clock.confirm(4602);
        assert!(clock.admit(4622, 200));
    }

    #[test]
    fn confirm_timestamps_unwrap_across_the_16_bit_boundary() {
        let clock = StreamClock::new();
        clock.sent(65_530);
        clock.confirm(65_532);
        assert_eq!(clock.backlog_ms(), Some(0));
        clock.sent(65_600);
        // Wire timestamp wrapped: 65_590 & 0xffff = 54.
        clock.confirm(54);
        assert_eq!(clock.backlog_ms(), Some(10));
        // An older (second) confirm never moves the confirmed position back.
        clock.confirm(65_531);
        assert_eq!(clock.backlog_ms(), Some(10));
    }

    #[test]
    fn recovery_budget_is_bounded_resettable_and_stops_on_close() {
        let mut cfg = Config::default();
        cfg.native.audio_recovery_attempts = 2;
        cfg.native.audio_recovery_interval_ms = 100;
        let stop = AtomicBool::new(false);
        let mut recovery = Recovery::new(&cfg);
        let t = Instant::now();
        assert!(recovery.wait(&stop));
        assert!(recovery.wait(&stop));
        assert!(!recovery.wait(&stop), "budget exhausted");
        assert!(t.elapsed() >= Duration::from_millis(200));
        recovery.reset();
        stop.store(true, Ordering::Release);
        assert!(!recovery.wait(&stop), "closing connection never waits");
        cfg.native.audio_recovery_attempts = 0;
        assert!(!Recovery::new(&cfg).wait(&AtomicBool::new(false)), "zero disables recovery");
    }

    #[test]
    fn writer_detects_a_vanished_fifo_reader() {
        let mut fds = [0; 2];
        // SAFETY: pipe2 fills two descriptors which are closed below.
        assert_eq!(unsafe { libc::pipe2(fds.as_mut_ptr(), libc::O_CLOEXEC | libc::O_NONBLOCK) }, 0);
        // SAFETY: the write end is owned only by this File.
        let writer = unsafe { <std::fs::File as std::os::fd::FromRawFd>::from_raw_fd(fds[1]) };
        assert!(!pipe_writer_lost(&writer), "reader still open");
        // SAFETY: closes the read end created above.
        unsafe { libc::close(fds[0]) };
        assert!(pipe_writer_lost(&writer), "reader closed must be detected without writing");
    }

    #[test]
    fn speaker_handler_offers_formats_and_stop_is_idempotent() {
        let stats = Arc::new(PipeStats::default());
        let mut handler = SpeakerHandler::new(Arc::new(Mutex::new(None)), Config::default(), Arc::clone(&stats));
        assert_eq!(handler.get_formats(), speaker_formats().as_slice());
        // NegotiatedFormat has no public constructor; start() is exercised live.
        handler.stop();
        assert_eq!(stats.speaker_workers.load(Ordering::Acquire), 0);
        assert!(handler.stream.is_none());
    }

    /// Opt-in: private PipeWire-Pulse lab only (MIOS_REMOTE_AUDIO_LAB=unix:/...pulse/native).
    /// A tone played into the session sink must reach the wave channel; never
    /// touches the operator's audio server or a physical device.
    #[test]
    #[ignore = "requires a private PipeWire-Pulse lab socket in MIOS_REMOTE_AUDIO_LAB"]
    fn speaker_sink_tone_reaches_rdpsnd_waves_in_private_lab() {
        use std::io::Write;
        use std::process::{Command, Stdio};
        let server = std::env::var("MIOS_REMOTE_AUDIO_LAB").expect("MIOS_REMOTE_AUDIO_LAB");
        assert!(server.starts_with("unix:/") && !server.contains("/run/user/1000/pulse/"), "private lab only");
        let lab_dir = std::path::Path::new(server.trim_start_matches("unix:")).parent().unwrap().parent().unwrap().to_path_buf();
        let mut cfg = Config::default();
        cfg.native.audio_server = server.clone();
        cfg.runtime_dir = lab_dir.join(format!("speaker-unit-{}", std::process::id()));
        cfg.naming.speaker_sink_prefix = "mios-speaker-unit".into();
        cfg.validate().unwrap();
        let stats = Arc::new(PipeStats::default());
        let stop = Arc::new(AtomicBool::new(false));
        let clock = Arc::new(StreamClock::new());
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
        let format = speaker_formats()[0].clone();
        spawn_speaker(cfg.clone(), Arc::clone(&stats), format, Arc::clone(&stop), clock, tx).unwrap();
        let deadline = Instant::now() + Duration::from_secs(6);
        while !stats.speaker_ready.load(Ordering::Acquire) && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(10));
        }
        assert!(stats.speaker_ready.load(Ordering::Acquire), "{:?}", stats.speaker_error.lock().unwrap());
        let sink = stats.speaker_sink.lock().unwrap().clone().unwrap();
        let mut play = Command::new("pacat")
            .args([format!("--device={sink}"), "--raw".into(), "--format=s16le".into(), "--rate=48000".into(), "--channels=2".into()])
            .env("PULSE_SERVER", &server)
            .stdin(Stdio::piped())
            .spawn()
            .unwrap();
        let tone: Vec<u8> = (0..48000 * 2)
            .flat_map(|i: u32| {
                let s = (12000.0 * (std::f64::consts::TAU * 440.0 * f64::from(i) / 48000.0).sin()) as i16;
                [s.to_le_bytes(), s.to_le_bytes()].concat()
            })
            .collect();
        play.stdin.take().unwrap().write_all(&tone).unwrap();
        let _ = play.wait();
        let mut samples = Vec::new();
        while let Ok(event) = rx.try_recv() {
            if let ServerEvent::Rdpsnd(RdpsndServerMessage::Wave(data, _)) = event {
                samples.extend(data.chunks_exact(2).map(|v| i16::from_le_bytes([v[0], v[1]])));
            }
        }
        stop.store(true, Ordering::Release);
        let deadline = Instant::now() + Duration::from_secs(8);
        while stats.speaker_workers.load(Ordering::Acquire) > 0 && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(10));
        }
        let modules = audio_control(&cfg, &["list".into(), "short".into(), "sinks".into()]).unwrap();
        assert!(!modules.contains(&sink), "session sink must be removed on close");
        let rms = (samples.iter().map(|v| f64::from(*v).powi(2)).sum::<f64>() / samples.len().max(1) as f64).sqrt();
        assert!(samples.len() >= 48000 && rms > 1000.0, "samples={} rms={rms}", samples.len());
        println!("speaker sink -> rdpsnd waves: samples={} rms={rms:.2} waves={}", samples.len(), stats.speaker_waves.load(Ordering::Relaxed));
        let _ = std::fs::remove_dir(cfg.runtime_dir.join("mios-rdp"));
        let _ = std::fs::remove_dir(&cfg.runtime_dir);
    }
}

/// Bounded-queue and underrun regressions. Kept in their own module and on
/// APIs the pre-fix code also has, so the same tests run as negative controls
/// against it.
#[cfg(test)]
mod bounded_tests {
    use super::*;

    /// Ten seconds of 20 ms waves to a client that never sends a Wave
    /// Confirm (or whose connection stalled before its first one): the queue
    /// must stay bounded instead of growing at the stream's byte rate.
    #[test]
    fn speaker_queue_stays_bounded_for_a_client_that_never_confirms() {
        let clock = StreamClock::new();
        let mut admitted = 0u32;
        for i in 0..500u32 {
            let now = 10_000 + i * 20;
            if clock.admit(now, 200) {
                clock.sent(now);
                admitted += 1;
            }
        }
        // 1 s start-up window (51 waves) plus probes at +1 s, +2 s and +4 s.
        assert!(admitted <= 60, "admitted {admitted} of 500 waves without a single confirm");
        assert!(admitted >= 50, "the start-up window must still play: admitted {admitted}");
    }

    /// The tail of a sound shorter than a packet must reach the client
    /// (padded with silence) when the sink goes quiet, not be discarded.
    #[test]
    fn speaker_flushes_a_partial_packet_when_the_sink_goes_quiet() {
        use std::io::Write;
        let mut fds = [0; 2];
        // SAFETY: pipe2 fills two descriptors, each owned by one File below.
        assert_eq!(unsafe { libc::pipe2(fds.as_mut_ptr(), libc::O_CLOEXEC | libc::O_NONBLOCK) }, 0);
        // SAFETY: each descriptor is owned only by its File.
        let reader = unsafe { <std::fs::File as std::os::fd::FromRawFd>::from_raw_fd(fds[0]) };
        let mut writer = unsafe { <std::fs::File as std::os::fd::FromRawFd>::from_raw_fd(fds[1]) };
        let mut cfg = Config::default();
        cfg.native.speaker_idle_ms = 0;
        let packet = 3840;
        let tail = vec![0x11u8; 1000];
        writer.write_all(&tail).unwrap();
        let stats = Arc::new(PipeStats::default());
        let stop = Arc::new(AtomicBool::new(false));
        let clock = Arc::new(StreamClock::new());
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
        let worker = {
            let (stats, stop, clock) = (Arc::clone(&stats), Arc::clone(&stop), Arc::clone(&clock));
            std::thread::spawn(move || speaker_pump(&cfg, &stats, &stop, &clock, &tx, &reader, packet).map_err(|e| e.to_string()))
        };
        std::thread::sleep(Duration::from_millis(300));
        stop.store(true, Ordering::Release);
        worker.join().unwrap().unwrap();
        drop(writer);
        let mut waves = Vec::new();
        while let Ok(event) = rx.try_recv() {
            if let ServerEvent::Rdpsnd(RdpsndServerMessage::Wave(data, _)) = event {
                waves.push(data);
            }
        }
        assert_eq!(waves.len(), 1, "the partial tail must be sent exactly once");
        assert_eq!(waves[0].len(), packet, "waves stay whole packets");
        assert_eq!(&waves[0][..tail.len()], tail.as_slice());
        assert!(waves[0][tail.len()..].iter().all(|b| *b == 0), "padding is silence");
    }
}
