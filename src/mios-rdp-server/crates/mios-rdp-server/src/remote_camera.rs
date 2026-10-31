// AI-hint: Connection-local RDPECAM server channels feed native PipeWire Video/Source nodes through bounded decoder pipes. Only client-selected cameras are advertised; connection close removes its nodes.
// AI-related: config.rs, pipeline.rs, main.rs, src/mios-rdp-server/prepare.rs, usr/share/mios/mios.toml
// AI-functions: Factory, Enumerator, Camera, Worker, select_media

use crate::{config::Config, pipeline::PipeStats};
use ironrdp_core::{decode, impl_as_any};
use ironrdp_dvc::{
    DrdynvcServer, DvcMessage, DvcProcessor, DvcServerProcessor, encode_dvc_messages,
};
use ironrdp_pdu::{PduResult, pdu_other_err};
use ironrdp_rdpecam::pdu::{DeviceMessage, DevicePdu, EnumerationPdu};
use ironrdp_rdpecam::{
    ENUMERATION_CHANNEL_NAME, MediaFormat, MediaType, ProtocolVersion, StartStreamInfo,
    StreamDescription,
};
use ironrdp_server::{Acceptor, ServerEvent, StaticChannelFactory};
use ironrdp_svc::ChannelFlags;
use serde_json::json;
use std::collections::BTreeMap;
use std::io::{Read, Write};
use std::process::{Command, Stdio};
use std::sync::{
    Arc, Mutex,
    atomic::{AtomicBool, Ordering},
    mpsc,
};
use std::time::{Duration, Instant};

type Sender = tokio::sync::mpsc::UnboundedSender<ServerEvent>;
pub struct Factory {
    pub cfg: Config,
    pub stats: Arc<PipeStats>,
}
impl StaticChannelFactory for Factory {
    fn attach(&self, _: &mut Acceptor) {}
    fn attach_dynamic(&self, channels: &mut DrdynvcServer, sender: Sender) {
        if !self.cfg.redirect.camera {
            return;
        }
        let enumerator = Enumerator {
            cfg: self.cfg.clone(),
            stats: self.stats.clone(),
            sender,
            alive: Arc::new(AtomicBool::new(true)),
            version: None,
            capture_owner: Arc::new(Mutex::new(None)),
            devices: Arc::new(Mutex::new(BTreeMap::new())),
            total: 0,
        };
        let old = std::mem::replace(channels, DrdynvcServer::new());
        *channels = old.with_dynamic_channel(enumerator);
    }
}
struct Enumerator {
    cfg: Config,
    stats: Arc<PipeStats>,
    sender: Sender,
    alive: Arc<AtomicBool>,
    version: Option<ProtocolVersion>,
    devices: Arc<Mutex<BTreeMap<String, u32>>>,
    total: usize,
    capture_owner: Arc<Mutex<Option<u32>>>,
}
impl_as_any!(Enumerator);
impl DvcServerProcessor for Enumerator {}
impl Drop for Enumerator {
    fn drop(&mut self) {
        self.alive.store(false, Ordering::Release);
    }
}
impl DvcProcessor for Enumerator {
    fn channel_name(&self) -> &str {
        ENUMERATION_CHANNEL_NAME
    }
    fn start(&mut self, _: u32) -> PduResult<Vec<DvcMessage>> {
        Ok(vec![])
    }
    fn close(&mut self, _: u32) {
        self.alive.store(false, Ordering::Release);
        let ids: Vec<_> = self.devices.lock().unwrap().values().copied().collect();
        let owner = self.alive.clone();
        let _ = self
            .sender
            .send(ServerEvent::DvcAction(Box::new(move |dvc| {
                let mut messages = Vec::new();
                for id in ids {
                    let owned = dvc.dvc_by_id::<Camera>(id).is_some_and(|camera| {
                        Arc::ptr_eq(&camera.processor().session_alive, &owner)
                    });
                    if owned {
                        messages.extend(dvc.close_channel(id));
                    }
                }
                Ok(messages)
            })));
    }
    fn process(&mut self, _: u32, payload: &[u8]) -> PduResult<Vec<DvcMessage>> {
        if payload.len() > 4096 {
            self.error("oversized camera enumeration PDU");
            return Ok(vec![]);
        }
        let pdu: EnumerationPdu = match decode(payload) {
            Ok(pdu) => pdu,
            Err(_) => {
                self.error("malformed camera enumeration PDU");
                return Ok(vec![]);
            }
        };
        match pdu {
            EnumerationPdu::SelectVersionRequest(version) if self.version.is_none() => {
                self.version = Some(version);
                return Ok(vec![Box::new(EnumerationPdu::SelectVersionResponse(
                    version,
                ))]);
            }
            EnumerationPdu::DeviceAdded { version, device } if self.version == Some(version) => {
                if self
                    .devices
                    .lock()
                    .unwrap()
                    .contains_key(&device.channel_name)
                {
                    return Ok(vec![]);
                }
                if self.total >= self.cfg.native.camera_max_devices {
                    self.error("camera device limit reached");
                    return Ok(vec![]);
                }
                self.total += 1;
                self.devices
                    .lock()
                    .unwrap()
                    .insert(device.channel_name.clone(), u32::MAX);
                let capture_owner = self.capture_owner.clone();
                let cfg = self.cfg.clone();
                let stats = self.stats.clone();
                let sender = self.sender.clone();
                let alive = self.alive.clone();
                let devices = self.devices.clone();
                self.sender
                    .send(ServerEvent::DvcAction(Box::new(move |dvc| {
                        if !alive.load(Ordering::Acquire)
                            || !devices.lock().unwrap().contains_key(&device.channel_name)
                        {
                            return Ok(vec![]);
                        }
                        let name = device.channel_name.clone();
                        let message = dvc.create_channel_with(|id| {
                            let mut camera = Camera::new(
                                cfg,
                                stats,
                                sender,
                                alive,
                                version,
                                device.channel_name,
                                device.display_name,
                            );
                            camera.id = id;
                            camera.capture_owner = capture_owner;
                            camera.siblings = devices.clone();
                            devices.lock().unwrap().insert(name, id);
                            Ok::<_, ironrdp_pdu::PduError>(camera)
                        })?;
                        Ok(vec![message])
                    })))
                    .map_err(|_| pdu_other_err!("camera session event channel closed"))?;
            }
            EnumerationPdu::DeviceRemoved {
                version,
                channel_name,
            } if self.version == Some(version) => {
                let devices = self.devices.clone();
                let alive = self.alive.clone();
                self.sender
                    .send(ServerEvent::DvcAction(Box::new(move |dvc| {
                        if !alive.load(Ordering::Acquire) {
                            return Ok(vec![]);
                        }
                        Ok(devices
                            .lock()
                            .unwrap()
                            .remove(&channel_name)
                            .and_then(|id| dvc.close_channel(id))
                            .into_iter()
                            .collect())
                    })))
                    .map_err(|_| pdu_other_err!("camera removal event channel closed"))?;
            }
            _ => {
                self.error("unexpected camera enumeration message");
            }
        }
        Ok(vec![])
    }
}
impl Enumerator {
    fn error(&self, error: &str) {
        *self.stats.camera_error.lock().unwrap() = Some(error.into());
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum State {
    Waiting,
    Activating,
    Streams,
    Formats,
    Starting,
    Streaming,
    Stopping,
    Deactivating,
    Dormant,
    Failed,
}
struct Camera {
    cfg: Config,
    stats: Arc<PipeStats>,
    sender: Sender,
    session_alive: Arc<AtomicBool>,
    stop: Arc<AtomicBool>,
    version: ProtocolVersion,
    name: String,
    display_name: String,
    id: u32,
    state: State,
    stream: u8,
    media: Option<MediaType>,
    frames: Option<mpsc::SyncSender<Vec<u8>>>,
    outstanding: bool,
    wanted: bool,
    capture_owner: Arc<Mutex<Option<u32>>>,
    siblings: Arc<Mutex<BTreeMap<String, u32>>>,
    // True while another device of this connection owns the client's capture
    // hardware; the worker then suspends its frame timeout instead of failing.
    waiting: Arc<AtomicBool>,
}
impl_as_any!(Camera);
impl DvcServerProcessor for Camera {}
impl Drop for Camera {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
        self.release_capture();
    }
}
impl Camera {
    fn new(
        cfg: Config,
        stats: Arc<PipeStats>,
        sender: Sender,
        session_alive: Arc<AtomicBool>,
        version: ProtocolVersion,
        name: String,
        display_name: String,
    ) -> Self {
        Self {
            cfg,
            stats,
            sender,
            session_alive,
            stop: Arc::new(AtomicBool::new(false)),
            version,
            name,
            display_name,
            id: 0,
            state: State::Activating,
            stream: 0,
            media: None,
            frames: None,
            outstanding: false,
            wanted: true,
            capture_owner: Arc::new(Mutex::new(None)),
            siblings: Arc::new(Mutex::new(BTreeMap::new())),
            waiting: Arc::new(AtomicBool::new(false)),
        }
    }
    fn activate(&mut self) -> Vec<DvcMessage> {
        if !self.wanted || self.stop.load(Ordering::Acquire) {
            return vec![];
        }
        let mut owner = self.capture_owner.lock().unwrap();
        if owner.is_some() && *owner != Some(self.id) {
            self.state = State::Waiting;
            self.waiting.store(true, Ordering::Release);
            return vec![];
        }
        *owner = Some(self.id);
        drop(owner);
        self.waiting.store(false, Ordering::Release);
        self.state = State::Activating;
        self.message(DevicePdu::ActivateDeviceRequest)
    }
    fn release_capture(&self) {
        let mut owner = self.capture_owner.lock().unwrap();
        if *owner != Some(self.id) {
            return;
        }
        *owner = None;
        drop(owner);
        let alive = self.session_alive.clone();
        let siblings = self.siblings.clone();
        let _ = self
            .sender
            .send(ServerEvent::DvcAction(Box::new(move |dvc| {
                if !alive.load(Ordering::Acquire) {
                    return Ok(vec![]);
                }
                let ids: Vec<_> = siblings.lock().unwrap().values().copied().collect();
                for id in ids {
                    let Some(mut camera) = dvc.dvc_by_id_mut::<Camera>(id) else {
                        continue;
                    };
                    let camera = camera.processor_mut();
                    if camera.state == State::Waiting && camera.wanted {
                        let messages = camera.activate();
                        if !messages.is_empty() {
                            return encode_dvc_messages(id, messages, ChannelFlags::SHOW_PROTOCOL)
                                .map_err(|e| ironrdp_pdu::encode_err!(e));
                        }
                    }
                }
                Ok(vec![])
            })));
    }
    fn message(&self, pdu: DevicePdu) -> Vec<DvcMessage> {
        vec![Box::new(DeviceMessage::new(self.version, pdu))]
    }
    fn fail(&mut self, error: &str) -> Vec<DvcMessage> {
        tracing::warn!(device=%self.display_name, state=?self.state, error, "remote camera channel failed");
        *self.stats.camera_error.lock().unwrap() = Some(format!("{}: {error}", self.display_name));
        self.state = State::Failed;
        self.wanted = false;
        self.stop.store(true, Ordering::Release);
        self.outstanding = false;
        let id = self.id;
        let alive = self.session_alive.clone();
        let _ = self
            .sender
            .send(ServerEvent::DvcAction(Box::new(move |dvc| {
                if !alive.load(Ordering::Acquire) {
                    return Ok(vec![]);
                }
                Ok(dvc.close_channel(id).into_iter().collect())
            })));
        vec![]
    }
    fn request(&mut self) -> Vec<DvcMessage> {
        if self.state != State::Streaming || self.outstanding {
            return vec![];
        }
        self.outstanding = true;
        self.message(DevicePdu::SampleRequest(self.stream))
    }
    fn demand(&mut self, active: bool) -> Vec<DvcMessage> {
        if self.stop.load(Ordering::Acquire) {
            return vec![];
        }
        self.wanted = active;
        match (active, self.state) {
            (false, State::Streaming) => {
                self.state = State::Stopping;
                self.outstanding = false;
                self.message(DevicePdu::StopStreamsRequest)
            }
            (true, State::Dormant | State::Waiting) => self.activate(),
            _ => vec![],
        }
    }
}
impl DvcProcessor for Camera {
    fn channel_name(&self) -> &str {
        &self.name
    }
    fn start(&mut self, id: u32) -> PduResult<Vec<DvcMessage>> {
        self.id = id;
        Ok(self.activate())
    }
    fn close(&mut self, _: u32) {
        self.stop.store(true, Ordering::Release);
    }
    fn process(&mut self, _: u32, payload: &[u8]) -> PduResult<Vec<DvcMessage>> {
        if payload.len() > 8 * 1024 * 1024 {
            return Ok(self.fail("oversized camera PDU"));
        }
        let msg: DeviceMessage = match decode(payload) {
            Ok(msg) => msg,
            Err(_) => return Ok(self.fail("malformed camera device PDU")),
        };
        if msg.version != self.version {
            return Ok(self.fail("camera protocol version changed"));
        }
        let out = match msg.pdu {
            DevicePdu::SampleErrorResponse { .. } if self.state != State::Streaming => vec![],
            DevicePdu::ErrorResponse(_) | DevicePdu::SampleErrorResponse { .. } => {
                self.fail("client camera permission or capture failed")
            }
            DevicePdu::SuccessResponse => match self.state {
                State::Activating if self.media.is_some() => {
                    self.state = State::Starting;
                    self.message(DevicePdu::StartStreamsRequest(vec![StartStreamInfo {
                        stream_index: self.stream,
                        media_type: self.media.unwrap(),
                    }]))
                }
                State::Activating => {
                    self.state = State::Streams;
                    self.message(DevicePdu::StreamListRequest)
                }
                State::Starting => {
                    self.state = State::Streaming;
                    if self.wanted {
                        self.request()
                    } else {
                        self.demand(false)
                    }
                }
                State::Stopping => {
                    self.state = State::Deactivating;
                    self.message(DevicePdu::DeactivateDeviceRequest)
                }
                State::Deactivating => {
                    self.state = State::Dormant;
                    self.release_capture();
                    if self.wanted {
                        self.demand(true)
                    } else {
                        vec![]
                    }
                }
                State::Failed => vec![],
                _ => self.fail("unexpected camera success response"),
            },
            DevicePdu::StreamListResponse(streams) if self.state == State::Streams => {
                if let Some(index) = streams
                    .iter()
                    .position(|s| s.frame_source_types & StreamDescription::COLOR != 0)
                    .filter(|i| *i <= u8::MAX as usize)
                {
                    self.stream = index as u8;
                    self.state = State::Formats;
                    self.message(DevicePdu::MediaTypeListRequest(self.stream))
                } else {
                    self.fail("client has no color camera stream")
                }
            }
            DevicePdu::MediaTypeListResponse(types) if self.state == State::Formats => {
                tracing::info!(device=%self.display_name, count=types.len(), offered=?types.iter().take(24).collect::<Vec<_>>(), "remote camera formats offered");
                if let Some(media) = select_media(&self.cfg, &types) {
                    tracing::info!(device=%self.display_name, ?media, "remote camera format selected");
                    self.media = Some(media);
                    let (tx, rx) = mpsc::sync_channel(2);
                    self.frames = Some(tx);
                    self.stats.camera_workers.fetch_add(1, Ordering::AcqRel);
                    let args = Worker {
                        cfg: self.cfg.clone(),
                        stats: self.stats.clone(),
                        sender: self.sender.clone(),
                        session_alive: self.session_alive.clone(),
                        stop: self.stop.clone(),
                        id: self.id,
                        media,
                        display_name: self.display_name.clone(),
                        request_queued: Arc::new(AtomicBool::new(false)),
                        waiting: self.waiting.clone(),
                    };
                    if let Err(error) = std::thread::Builder::new()
                        .name("mios-remote-camera".into())
                        .spawn(move || args.run(rx))
                    {
                        self.stats.camera_workers.fetch_sub(1, Ordering::AcqRel);
                        return Ok(self.fail(&format!("camera worker: {error}")));
                    }
                    self.state = State::Starting;
                    self.message(DevicePdu::StartStreamsRequest(vec![StartStreamInfo {
                        stream_index: self.stream,
                        media_type: media,
                    }]))
                } else {
                    self.fail("no supported camera format within configured resolution/FPS bounds")
                }
            }
            DevicePdu::SampleResponse {
                stream_index,
                sample,
            } if self.state == State::Streaming => {
                if !self.outstanding
                    || stream_index != self.stream
                    || !self.media.is_some_and(|m| m.validate_sample(&sample))
                {
                    self.fail("unsolicited, wrong-stream or malformed camera frame")
                } else {
                    self.outstanding = false;
                    if self
                        .frames
                        .as_ref()
                        .is_some_and(|tx| tx.try_send(sample).is_ok())
                    {
                        vec![]
                    } else {
                        self.fail("camera decoder queue closed or full")
                    }
                }
            }
            DevicePdu::SampleResponse { .. }
                if matches!(
                    self.state,
                    State::Stopping | State::Deactivating | State::Dormant | State::Waiting
                ) =>
            {
                vec![]
            }
            _ => self.fail("unexpected camera state transition"),
        };
        Ok(out)
    }
}
fn select_media(cfg: &Config, types: &[MediaType]) -> Option<MediaType> {
    types
        .iter()
        .copied()
        .filter(|m| {
            m.width >= 16
                && m.height >= 16
                && ((m.width <= cfg.native.camera_max_width
                    && m.height <= cfg.native.camera_max_height)
                    || (m.height <= cfg.native.camera_max_width
                        && m.width <= cfg.native.camera_max_height))
                && m.width % 4 == 0
                && m.height % 4 == 0
                && m.frame_rate_denominator > 0
                && m.frame_rate_numerator > 0
                && u64::from(m.frame_rate_numerator)
                    <= u64::from(cfg.native.camera_max_fps) * u64::from(m.frame_rate_denominator)
                && matches!(
                    m.format,
                    MediaFormat::H264
                        | MediaFormat::Mjpg
                        | MediaFormat::Nv12
                        | MediaFormat::I420
                        | MediaFormat::Yuy2
                )
        })
        .max_by_key(|m| {
            let codec = match m.format {
                MediaFormat::H264 => 5,
                MediaFormat::Mjpg => 4,
                MediaFormat::Nv12 => 3,
                MediaFormat::I420 => 2,
                _ => 1,
            };
            (
                codec,
                u64::from(m.width) * u64::from(m.height),
                u64::from(m.frame_rate_numerator) * 1000 / u64::from(m.frame_rate_denominator),
            )
        })
}
struct Worker {
    cfg: Config,
    stats: Arc<PipeStats>,
    sender: Sender,
    session_alive: Arc<AtomicBool>,
    stop: Arc<AtomicBool>,
    id: u32,
    media: MediaType,
    display_name: String,
    request_queued: Arc<AtomicBool>,
    waiting: Arc<AtomicBool>,
}
impl Worker {
    fn action(&self, kind: u8, active: bool) {
        if kind == 1 && self.request_queued.swap(true, Ordering::AcqRel) {
            return;
        }
        let id = self.id;
        let alive = self.session_alive.clone();
        let stop = self.stop.clone();
        let queued = self.request_queued.clone();
        let sent = self
            .sender
            .send(ServerEvent::DvcAction(Box::new(move |dvc| {
                if kind == 1 {
                    queued.store(false, Ordering::Release);
                }
                if !alive.load(Ordering::Acquire) {
                    return Ok(vec![]);
                }
                if kind == 2 {
                    return Ok(dvc.close_channel(id).into_iter().collect());
                }
                if stop.load(Ordering::Acquire) {
                    return Ok(vec![]);
                }
                let Some(mut camera) = dvc.dvc_by_id_mut::<Camera>(id) else {
                    return Ok(vec![]);
                };
                let messages = if kind == 0 {
                    camera.processor_mut().demand(active)
                } else {
                    camera.processor_mut().request()
                };
                encode_dvc_messages(id, messages, ChannelFlags::SHOW_PROTOCOL)
                    .map_err(|e| ironrdp_pdu::encode_err!(e))
            })));
        if sent.is_err() && kind == 1 {
            self.request_queued.store(false, Ordering::Release);
        }
    }
    fn run(self, rx: mpsc::Receiver<Vec<u8>>) {
        let generation = self.stats.camera_generation.fetch_add(1, Ordering::AcqRel);
        let name = format!(
            "{}-{}-{generation}",
            self.cfg.naming.camera_source_prefix,
            std::process::id()
        );
        // A consumer whose buffer negotiation fails makes pipewiresink post an
        // error and the provider exit; that must not destroy the remote camera.
        // Restart the provider (bounded) and restart the client stream so the
        // decoder gets a fresh key frame.
        let mut restarts = 0u32;
        let result = loop {
            let started = Instant::now();
            let result = self.run_video(&rx, &name);
            let restartable =
                matches!(&result, Err(e) if e.to_string().starts_with(PROVIDER_EXITED));
            if !restartable
                || !self.session_alive.load(Ordering::Acquire)
                || self.stop.load(Ordering::Acquire)
            {
                break result;
            }
            if started.elapsed() > Duration::from_secs(30) {
                restarts = 0;
            }
            if restarts >= self.cfg.native.camera_provider_restarts {
                break result;
            }
            restarts += 1;
            self.stats
                .camera_sources
                .lock()
                .unwrap()
                .retain(|v| v["name"] != name);
            self.stats.camera_restarts.fetch_add(1, Ordering::AcqRel);
            tracing::warn!(device=%self.display_name, error=%result.unwrap_err(), restarts, "restarting native camera provider");
            // Stop, then want again: the client restarts its stream (a fresh
            // key frame) and the new provider is primed exactly like the first.
            self.action(0, false);
            self.action(0, true);
            while rx.try_recv().is_ok() {}
        };
        if let Err(error) = result {
            tracing::warn!(device=%self.display_name, error=%error, "native camera provider failed");
            *self.stats.camera_error.lock().unwrap() =
                Some(format!("{}: {error}", self.display_name));
            self.action(2, false);
        }
        self.stop.store(true, Ordering::Release);
        self.stats
            .camera_sources
            .lock()
            .unwrap()
            .retain(|v| v["name"] != name);
        self.stats.camera_workers.fetch_sub(1, Ordering::AcqRel);
    }
    fn read_graph(&self, name: &str) -> anyhow::Result<Option<Vec<u8>>> {
        let mut child = Command::new(&self.cfg.native.camera_graph_program)
            .args(["-N", name])
            .env("PIPEWIRE_RUNTIME_DIR", pipewire_runtime(&self.cfg))
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()?;
        let result = (|| -> anyhow::Result<Option<Vec<u8>>> {
            let mut output = child
                .stdout
                .take()
                .ok_or_else(|| anyhow::anyhow!("camera graph pipe missing"))?;
            nonblocking(&output)?;
            let mut result = Vec::new();
            let deadline = Instant::now() + Duration::from_secs(2);
            loop {
                if self.stop.load(Ordering::Acquire) || !self.session_alive.load(Ordering::Acquire)
                {
                    return Ok(None);
                }
                if Instant::now() >= deadline {
                    anyhow::bail!("native camera graph inspection timed out");
                }
                let mut bytes = [0u8; 8192];
                match output.read(&mut bytes) {
                    Ok(0) => {
                        if let Some(status) = child.try_wait()? {
                            if !status.success() {
                                anyhow::bail!("native camera graph inspection failed");
                            }
                            return Ok(Some(result));
                        }
                        std::thread::sleep(Duration::from_millis(5));
                    }
                    Ok(n) => {
                        if result.len() + n > 1024 * 1024 {
                            anyhow::bail!("camera graph response exceeds bound");
                        }
                        result.extend_from_slice(&bytes[..n]);
                    }
                    Err(e)
                        if matches!(
                            e.kind(),
                            std::io::ErrorKind::WouldBlock | std::io::ErrorKind::Interrupted
                        ) =>
                    {
                        std::thread::sleep(Duration::from_millis(5))
                    }
                    Err(e) => return Err(e.into()),
                }
            }
        })();
        let _ = child.kill();
        let _ = child.wait();
        result
    }
    fn run_video(&self, rx: &mpsc::Receiver<Vec<u8>>, name: &str) -> anyhow::Result<()> {
        let mut cmd = Command::new(&self.cfg.native.camera_video_program);
        if self.cfg.native.log_negotiation && std::env::var_os("GST_DEBUG").is_none() {
            cmd.env("GST_DEBUG", "2");
        }
        cmd.args(pipeline_args(self.media, name, &self.display_name))
            .env("PIPEWIRE_RUNTIME_DIR", pipewire_runtime(&self.cfg))
            .stdin(Stdio::piped())
            .stdout(Stdio::null())
            .stderr(Stdio::inherit());
        die_with_spawner(&mut cmd);
        let mut child = cmd.spawn()?;
        let result = (|| -> anyhow::Result<()> {
            let mut input = child
                .stdin
                .take()
                .ok_or_else(|| anyhow::anyhow!("camera decoder stdin missing"))?;
            nonblocking(&input)?;
            let mut pending: Option<(Vec<u8>, usize)> = None;
            let mut poll_at = Instant::now();
            let mut next_frame = Instant::now();
            let mut desired = true;
            let mut node_seen = false;
            let started = Instant::now();
            let mut last_sample = Instant::now();
            let interval = Duration::from_secs_f64(
                self.media.frame_rate_denominator as f64 / self.media.frame_rate_numerator as f64,
            );
            while self.session_alive.load(Ordering::Acquire) && !self.stop.load(Ordering::Acquire) {
                if let Some(status) = child.try_wait()? {
                    anyhow::bail!("{PROVIDER_EXITED}: {status}");
                }
                if Instant::now() >= poll_at {
                    let Some(output) = self.read_graph(name)? else {
                        break;
                    };
                    if let Some((id, active, formats_ready)) = video_node(&output, name) {
                        // A provider node exists before its first decoded caps.
                        // Stopping capture here leaves camera consumers with an
                        // empty format list and no way to request a stream.
                        if !formats_ready {
                            if started.elapsed() > Duration::from_millis(self.cfg.native.camera_timeout_ms) {
                                anyhow::bail!("native camera did not publish usable video formats");
                            }
                        }
                        if formats_ready {
                        node_seen = true;
                        let mut nodes = self.stats.camera_sources.lock().unwrap();
                        let capture = if self.waiting.load(Ordering::Acquire) {
                            "waiting"
                        } else if active {
                            "active"
                        } else {
                            "idle"
                        };
                        let entry = json!({"name":name,"node_id":id,"active":active,"capture":capture,"device":self.display_name,"description":camera_description(&self.display_name),"width":self.media.width,"height":self.media.height,"format":format!("{:?}",self.media.format),"native_format":NATIVE_FORMAT,"consumers":consumers(&self.cfg)});
                        if let Some(old) = nodes.iter_mut().find(|v| v["name"] == name) {
                            *old = entry;
                        } else {
                            nodes.push(entry);
                        }
                        if active != desired {
                            desired = active;
                            self.action(0, active);
                            if active {
                                last_sample = Instant::now();
                            }
                        }
                        }
                    }
                    poll_at =
                        Instant::now() + Duration::from_millis(self.cfg.native.camera_poll_ms);
                }
                if !node_seen
                    && started.elapsed() > Duration::from_millis(self.cfg.native.camera_timeout_ms)
                {
                    anyhow::bail!("native camera node failed to appear");
                }
                if self.waiting.load(Ordering::Acquire) {
                    // Exclusive client capture belongs to a sibling device;
                    // waiting for it is not a stalled camera.
                    last_sample = Instant::now();
                }
                if desired
                    && last_sample.elapsed()
                        > Duration::from_millis(self.cfg.native.camera_timeout_ms)
                {
                    anyhow::bail!("client camera frame timed out");
                }
                if pending.is_none() {
                    match rx.try_recv() {
                        Ok(packet) => {
                            last_sample = Instant::now();
                            pending = Some((packet, 0));
                        }
                        Err(mpsc::TryRecvError::Disconnected) => break,
                        Err(mpsc::TryRecvError::Empty) => {}
                    }
                }
                if let Some((packet, offset)) = pending.as_mut() {
                    match input.write(&packet[*offset..]) {
                        Ok(0) => anyhow::bail!("native camera decoder pipe closed"),
                        Ok(n) => {
                            *offset += n;
                            self.stats
                                .camera_bytes
                                .fetch_add(n as u64, Ordering::Relaxed);
                        }
                        Err(e)
                            if matches!(
                                e.kind(),
                                std::io::ErrorKind::WouldBlock | std::io::ErrorKind::Interrupted
                            ) => {}
                        Err(e) => return Err(e.into()),
                    }
                    if *offset == packet.len() {
                        pending = None;
                        self.stats.camera_frames.fetch_add(1, Ordering::Relaxed);
                        next_frame = Instant::now() + interval;
                    }
                } else if desired && Instant::now() >= next_frame {
                    self.action(1, true);
                    next_frame = Instant::now() + interval;
                }
                std::thread::sleep(Duration::from_millis(5));
            }
            Ok(())
        })();
        let _ = child.kill();
        let _ = child.wait();
        result
    }
}
fn pipeline_args(media: MediaType, name: &str, display_name: &str) -> Vec<String> {
    let mut args: Vec<String> = [
        "-q",
        "fdsrc",
        "fd=0",
        "blocksize=65536",
        "do-timestamp=true",
        "!",
    ]
    .into_iter()
    .map(String::from)
    .collect();
    let decode = match media.format {
        MediaFormat::H264 => vec![
            "h264parse".into(),
            "!".into(),
            "capssetter".into(),
            format!(
                "caps=video/x-h264,framerate=(fraction){}/{}",
                media.frame_rate_numerator, media.frame_rate_denominator
            ),
            "!".into(),
            "openh264dec".into(),
        ],
        MediaFormat::Mjpg => vec!["jpegparse".into(), "!".into(), "jpegdec".into()],
        format => vec![
            "rawvideoparse".into(),
            format!("width={}", media.width),
            format!("height={}", media.height),
            format!(
                "framerate={}/{}",
                media.frame_rate_numerator, media.frame_rate_denominator
            ),
            format!(
                "format={}",
                match format {
                    MediaFormat::Nv12 => "nv12",
                    MediaFormat::I420 => "i420",
                    _ => "yuy2",
                }
            ),
        ],
    };
    args.extend(decode);
    args.extend([
        "!".into(),
        "capssetter".into(),
        format!("caps=video/x-raw,framerate=(fraction){}/{}",media.frame_rate_numerator,media.frame_rate_denominator),
        "!".into(),
        "videoconvert".into(),
        "!".into(),
        "videoscale".into(),
        "add-borders=true".into(),
        "!".into(),
        output_caps(media),
        "!".into(),
    ]);
    if media.flags & MediaType::BOTTOM_UP_IMAGE != 0 && !media.is_compressed() {
        args.extend(
            ["videoflip", "method=vertical-flip", "!"]
                .into_iter()
                .map(String::from),
        );
    }
    // The internal node name carries connection ownership; the label is what
    // pickers show. media.role=Camera is what camera pickers filter on
    // (xdg-desktop-portal Camera, WirePlumber portal access, libwebrtc).
    let label = camera_description(display_name);
    args.extend(["pipewiresink".into(),"mode=provide".into(),"sync=false".into(),"async=false".into(),format!("stream-properties=\"props,node.name=(string){name},node.description=(string)\\\"{label}\\\",node.nick=(string)MiOS-Remote-Camera,media.class=(string)Video/Source,media.type=(string)Video,media.role=(string)Camera,media.category=(string)Capture,device.icon-name=(string)camera-web,node.virtual=(boolean)true\"")]);
    args
}
const PROVIDER_EXITED: &str = "native video process exited";
/// The decoded native format. Packed single-plane BGRA is what both OBS's
/// PipeWire camera copy path and PipeWire's V4L2 compatibility library
/// negotiate; planar NV12/I420 fail in each (see remote-camera.md).
const NATIVE_FORMAT: &str = "BGRA";
/// PipeWire runtime that owns the camera nodes: the operator's by default.
fn pipewire_runtime(cfg: &Config) -> std::path::PathBuf {
    if cfg.native.camera_pipewire_runtime.is_empty() {
        format!("/run/user/{}", cfg.operator_uid).into()
    } else {
        cfg.native.camera_pipewire_runtime.clone().into()
    }
}
/// Which consumer families can see a published camera node. `pipewire` and
/// `camera_portal` (media.role=Camera: xdg-desktop-portal Camera, Flatpak
/// apps and WebRTC browsers) are always true; `v4l2` needs the compatibility
/// library, used per application through the `mios-camera-v4l2` launcher.
pub(crate) fn consumers(cfg: &Config) -> serde_json::Value {
    let library = cfg.native.camera_v4l2
        && std::path::Path::new(&cfg.native.camera_v4l2_library).is_file();
    json!({
        "pipewire": true,
        "camera_portal": true,
        "v4l2": library,
        "v4l2_launcher": if library { Some("mios-camera-v4l2") } else { None },
        "v4l2_reason": if !cfg.native.camera_v4l2 {
            Some("remote.rdp.native.camera_v4l2 is false")
        } else if !library {
            Some("camera_v4l2_library is not installed (pipewire-v4l2)")
        } else {
            None
        },
    })
}
/// Human-readable picker label. Client device names are untrusted, so only
/// alphanumerics and spaces reach the GStreamer structure string.
fn camera_description(display_name: &str) -> String {
    let label: String = display_name
        .chars()
        .take(64)
        .map(|c| if c.is_alphanumeric() || c == ' ' { c } else { '-' })
        .collect();
    let label = label.trim();
    format!("{} (MiOS RDP)", if label.is_empty() { "Camera" } else { label })
}
// Normalize the decoded frame to the selected native source dimensions. A
// client may encode a larger camera frame than the negotiated media type.
fn output_caps(media: MediaType) -> String {
    format!(
        "video/x-raw,format={NATIVE_FORMAT},width={},height={}",
        media.width, media.height
    )
}
fn video_node(bytes: &[u8], name: &str) -> Option<(u64, bool, bool)> {
    let value: serde_json::Value = serde_json::from_slice(bytes).ok()?;
    value.as_array()?.iter().find_map(|v| {
        if v["type"] != "PipeWire:Interface:Node" || v["info"]["props"]["node.name"] != name {
            return None;
        }
        let ready = v["info"]["params"]["EnumFormat"].as_array().is_some_and(|formats| formats.iter().any(|f| f["mediaType"] == "video" && f["mediaSubtype"] == "raw" && f["size"]["width"].as_u64().unwrap_or(0) > 0 && f["size"]["height"].as_u64().unwrap_or(0) > 0 && f["framerate"]["num"].as_u64().unwrap_or(0) > 0 && f["framerate"]["denom"].as_u64().unwrap_or(0) > 0));
        Some((v["id"].as_u64()?, v["info"]["state"] == "running", ready))
    })
}
/// The native provider must not outlive the server thread that owns it: a
/// crashed or killed server would otherwise leave an orphaned, frameless
/// camera node visible in every picker. The kernel SIGKILLs the child when the
/// spawning thread exits; the worker thread keeps running for the provider's
/// whole life (including bounded restarts) and reaps it itself.
#[allow(unsafe_code)]
fn die_with_spawner(cmd: &mut Command) {
    use std::os::unix::process::CommandExt;
    let parent = std::process::id() as libc::pid_t;
    // SAFETY: the hook runs in the forked child before exec and calls only the
    // async-signal-safe prctl(2)/getppid(2); it allocates nothing.
    unsafe {
        cmd.pre_exec(move || {
            if libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGKILL) != 0 {
                return Err(std::io::Error::last_os_error());
            }
            // The server died between fork and prctl: do not start orphaned.
            if libc::getppid() != parent {
                return Err(std::io::Error::from_raw_os_error(libc::ESRCH));
            }
            Ok(())
        });
    }
}
#[allow(unsafe_code)]
fn nonblocking(input: &impl std::os::fd::AsRawFd) -> std::io::Result<()> {
    // This owned pipe FD stays valid for both fcntl calls; no borrowed memory crosses FFI.
    let flags = unsafe { libc::fcntl(input.as_raw_fd(), libc::F_GETFL) };
    if flags < 0
        || unsafe { libc::fcntl(input.as_raw_fd(), libc::F_SETFL, flags | libc::O_NONBLOCK) } < 0
    {
        return Err(std::io::Error::last_os_error());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use ironrdp_core::encode_vec;
    use ironrdp_dvc::DrdynvcClient;
    use ironrdp_rdpecam::{
        CameraBackend, CameraRedirectionSession, DeviceDescriptor, ErrorCode, SampleDelivery,
        SampleSink,
    };
    use ironrdp_svc::{SvcMessage, SvcProcessor};
    use std::collections::VecDeque;

    fn dimensions() -> (u32, u32) {
        if std::env::var("MIOS_RDP_CAMERA_TEST_PORTRAIT").as_deref() == Ok("1") {
            (240, 320)
        } else {
            (320, 240)
        }
    }
    fn encoded_dimensions() -> (u32, u32) {
        let (w, h) = dimensions();
        if std::env::var("MIOS_RDP_CAMERA_TEST_LARGE_CODED").as_deref() == Ok("1") {
            return (w * 2, h * 2);
        }
        if std::env::var("MIOS_RDP_CAMERA_TEST_TRANSPOSED").as_deref() == Ok("1") {
            (h, w)
        } else {
            (w, h)
        }
    }
    fn media(format: MediaFormat) -> MediaType {
        let (w, h) = dimensions();
        MediaType::new(format, w, h, 30, 1, 1, 1, 0).unwrap()
    }
    #[test]
    fn portrait_camera_uses_the_same_pixel_bounds_as_landscape() {
        let cfg = Config::default();
        for (w, h) in [(1280, 720), (720, 1280)] {
            let media = MediaType::new(MediaFormat::H264, w, h, 30000, 1000, 1, 1, 1).unwrap();
            assert_eq!(select_media(&cfg, &[media]), Some(media));
        }
        let too_large =
            MediaType::new(MediaFormat::H264, 1080, 1920, 30000, 1000, 1, 1, 1).unwrap();
        assert!(select_media(&cfg, &[too_large]).is_none());
    }
    #[test]
    fn decoded_output_caps_match_the_negotiated_native_source() {
        let media = MediaType::new(MediaFormat::H264, 720, 1280, 30, 1, 1, 1, 1).unwrap();
        assert_eq!(
            output_caps(media),
            "video/x-raw,format=BGRA,width=720,height=1280"
        );
        let raw = MediaType::new(MediaFormat::I420, 720, 1280, 30, 1, 1, 1, 0).unwrap();
        assert!(!output_caps(raw).contains(';'));
    }
    #[test]
    fn camera_capture_is_exclusive_and_released_for_the_next_device() {
        let mut first = camera();
        let mut second = camera();
        let owner = Arc::new(Mutex::new(None));
        first.capture_owner = owner.clone();
        second.capture_owner = owner.clone();
        assert!(!first.start(1).unwrap().is_empty());
        assert!(second.start(2).unwrap().is_empty());
        assert_eq!(second.state, State::Waiting);
        assert_eq!(*owner.lock().unwrap(), Some(1));
        first.wanted = false;
        first.state = State::Deactivating;
        response(&mut first, DevicePdu::SuccessResponse);
        assert!(!second.activate().is_empty());
        assert_eq!(*owner.lock().unwrap(), Some(2));
        drop(first);
        assert_eq!(*owner.lock().unwrap(), Some(2));
        drop(second);
        assert_eq!(*owner.lock().unwrap(), None);
    }
    #[test]
    fn late_sample_cancellation_does_not_destroy_an_idle_source() {
        let mut c = camera();
        c.state = State::Dormant;
        assert!(
            response(
                &mut c,
                DevicePdu::SampleErrorResponse {
                    stream_index: 0,
                    error: ErrorCode::InvalidRequest
                }
            )
            .is_empty()
        );
        assert_eq!(c.state, State::Dormant);
        assert!(!c.stop.load(Ordering::Acquire));
    }
    fn camera() -> Camera {
        let (tx, _rx) = tokio::sync::mpsc::unbounded_channel();
        Camera::new(
            Config::default(),
            Arc::new(PipeStats::default()),
            tx,
            Arc::new(AtomicBool::new(true)),
            ProtocolVersion::V2,
            "test-camera".into(),
            "Synthetic camera".into(),
        )
    }
    fn response(c: &mut Camera, pdu: DevicePdu) -> Vec<DvcMessage> {
        c.process(
            c.id,
            &encode_vec(&DeviceMessage::new(c.version, pdu)).unwrap(),
        )
        .unwrap()
    }
    fn pdu(messages: &[DvcMessage]) -> DevicePdu {
        decode::<DeviceMessage>(&encode_vec(messages[0].as_ref()).unwrap())
            .unwrap()
            .pdu
    }
    #[test]
    fn camera_media_selection_prefers_compression_and_enforces_limits() {
        let cfg = Config::default();
        assert_eq!(
            select_media(&cfg, &[media(MediaFormat::Nv12), media(MediaFormat::H264)])
                .unwrap()
                .format,
            MediaFormat::H264
        );
        for mut bad in [media(MediaFormat::Mjpg); 4].into_iter().enumerate() {
            match bad.0 {
                0 => bad.1.width = 4096,
                1 => bad.1.frame_rate_denominator = 0,
                2 => bad.1.frame_rate_numerator = 120,
                _ => bad.1.width = 319,
            };
            assert!(select_media(&cfg, &[bad.1]).is_none());
        }
    }
    #[test]
    fn camera_capture_demand_survives_start_stop_response_races() {
        let mut c = camera();
        c.media = Some(media(MediaFormat::I420));
        c.state = State::Starting;
        assert!(c.demand(false).is_empty());
        assert!(matches!(
            pdu(&response(&mut c, DevicePdu::SuccessResponse)),
            DevicePdu::StopStreamsRequest
        ));
        assert!(matches!(
            pdu(&response(&mut c, DevicePdu::SuccessResponse)),
            DevicePdu::DeactivateDeviceRequest
        ));
        assert!(c.demand(true).is_empty());
        assert!(matches!(
            pdu(&response(&mut c, DevicePdu::SuccessResponse)),
            DevicePdu::ActivateDeviceRequest
        ));
        assert!(matches!(
            pdu(&response(&mut c, DevicePdu::SuccessResponse)),
            DevicePdu::StartStreamsRequest(_)
        ));
        assert!(matches!(
            pdu(&response(&mut c, DevicePdu::SuccessResponse)),
            DevicePdu::SampleRequest(0)
        ));
        assert!(
            c.request().is_empty(),
            "only one camera sample may be outstanding"
        );
    }
    #[test]
    fn camera_rejects_unsolicited_and_malformed_samples() {
        for (outstanding, bytes) in [(false, 320 * 240 * 3 / 2), (true, 7)] {
            let mut c = camera();
            c.media = Some(media(MediaFormat::I420));
            c.state = State::Streaming;
            c.outstanding = outstanding;
            response(
                &mut c,
                DevicePdu::SampleResponse {
                    stream_index: 0,
                    sample: vec![0; bytes],
                },
            );
            assert_eq!(c.state, State::Failed);
            assert!(c.stop.load(Ordering::Acquire));
        }
    }
    #[test]
    fn camera_node_inspection_requires_its_exact_name_and_type() {
        let bytes=br#"[{"id":8,"type":"PipeWire:Interface:Node","info":{"state":"running","props":{"node.name":"mios-test"}}}]"#;
        assert_eq!(video_node(bytes, "mios-test"), Some((8, true, false)));
        assert_eq!(video_node(bytes, "other"), None);
        assert_eq!(video_node(b"invalid", "mios-test"), None);
    }
    #[test]
    fn camera_publication_requires_usable_advertised_video_formats() {
        let mut graph = json!([{"id":8,"type":"PipeWire:Interface:Node","info":{"state":"suspended","props":{"node.name":"mios-test"},"params":{"EnumFormat":[{"mediaType":"video","mediaSubtype":"raw","format":"I420","size":{"width":720,"height":1280},"framerate":{"num":30,"denom":1}}]}}}]);
        assert_eq!(video_node(&serde_json::to_vec(&graph).unwrap(), "mios-test"), Some((8,false,true)));
        graph[0]["info"]["params"]["EnumFormat"][0]["framerate"]["num"] = json!(0);
        assert_eq!(video_node(&serde_json::to_vec(&graph).unwrap(), "mios-test"), Some((8,false,false)));
        let args=pipeline_args(media(MediaFormat::H264),"mios-test-9","Front Camera,evil=(string)bad!");
        let props=args.last().unwrap();
        assert!(props.contains("node.description=(string)\\\"Front Camera-evil--string-bad- (MiOS RDP)\\\""));
        assert!(!props.contains("evil=(string)"));
    }
    #[test]
    fn camera_node_carries_the_picker_role_and_a_bounded_label() {
        let args = pipeline_args(media(MediaFormat::Mjpg), "mios-remote-camera-1-0", "Back \"Camera\",x=(int)1\\");
        let props = args.last().unwrap();
        assert!(props.contains(",media.role=(string)Camera,"));
        assert!(props.contains(",media.class=(string)Video/Source,"));
        assert!(props.contains("node.description=(string)\\\"Back -Camera--x--int-1- (MiOS RDP)\\\""));
        assert_eq!(props.matches('"').count(), 4, "only the wrapper and label quotes: {props}");
        assert_eq!(camera_description("  "), "Camera (MiOS RDP)");
        assert_eq!(camera_description(&"x".repeat(80)).len(), 64 + " (MiOS RDP)".len());
    }
    #[test]
    fn one_single_plane_native_format_serves_pipewire_obs_and_v4l2() {
        let media = MediaType::new(MediaFormat::H264, 640, 480, 30, 1, 1, 1, 1).unwrap();
        let args = pipeline_args(media, "n", "Front");
        assert!(args.iter().any(|a| a == "video/x-raw,format=BGRA,width=640,height=480"));
        assert!(!args.iter().any(|a| a.contains("format=NV12") || a.contains("format=I420")));
        let mut cfg = Config::default();
        cfg.native.camera_v4l2 = false;
        assert_eq!(consumers(&cfg)["v4l2"], false);
        assert_eq!(consumers(&cfg)["pipewire"], true);
        assert_eq!(consumers(&cfg)["v4l2_reason"], "remote.rdp.native.camera_v4l2 is false");
        cfg.native.camera_v4l2 = true;
        cfg.native.camera_v4l2_library = "/nonexistent/libpw-v4l2.so".into();
        assert_eq!(consumers(&cfg)["v4l2"], false);
        assert_eq!(consumers(&cfg)["camera_portal"], true);
        let library = std::env::temp_dir().join(format!("mios-pw-v4l2-{}.so", std::process::id()));
        std::fs::write(&library, b"").unwrap();
        cfg.native.camera_v4l2_library = library.display().to_string();
        let seen = consumers(&cfg);
        std::fs::remove_file(&library).unwrap();
        assert_eq!(seen["v4l2"], true);
        assert_eq!(seen["v4l2_launcher"], "mios-camera-v4l2");
    }
    #[test]
    fn provider_dies_with_its_spawning_thread() {
        fn spawn(guard: bool) -> std::process::Child {
            std::thread::spawn(move || {
                let mut cmd = Command::new("sleep");
                cmd.arg("30").stdout(Stdio::null());
                if guard {
                    die_with_spawner(&mut cmd);
                }
                cmd.spawn().unwrap()
            })
            .join()
            .unwrap()
        }
        use std::os::unix::process::ExitStatusExt;
        let mut guarded = spawn(true);
        let deadline = Instant::now() + Duration::from_secs(5);
        let status = loop {
            if let Some(status) = guarded.try_wait().unwrap() {
                break status;
            }
            assert!(Instant::now() < deadline, "guarded provider outlived its spawner");
            std::thread::sleep(Duration::from_millis(10));
        };
        assert_eq!(status.signal(), Some(libc::SIGKILL));
        // Negative control: the same spawn without the guard keeps running.
        let mut unguarded = spawn(false);
        std::thread::sleep(Duration::from_millis(300));
        assert!(unguarded.try_wait().unwrap().is_none(), "an unguarded child survives its spawner");
        unguarded.kill().unwrap();
        unguarded.wait().unwrap();
    }
    #[test]
    fn camera_nodes_follow_the_configured_pipewire_runtime() {
        let mut cfg = Config::default();
        assert_eq!(pipewire_runtime(&cfg), std::path::PathBuf::from("/run/user/1000"));
        cfg.native.camera_pipewire_runtime = "/run/user/1000/mios-nested-desktop-7".into();
        assert_eq!(pipewire_runtime(&cfg), std::path::PathBuf::from("/run/user/1000/mios-nested-desktop-7"));
    }
    #[test]
    fn a_device_waiting_for_exclusive_capture_reports_waiting() {
        let mut first = camera();
        let mut second = camera();
        let owner = Arc::new(Mutex::new(None));
        first.capture_owner = owner.clone();
        second.capture_owner = owner.clone();
        assert!(!first.start(1).unwrap().is_empty());
        assert!(!first.waiting.load(Ordering::Acquire));
        assert!(second.start(2).unwrap().is_empty());
        assert!(second.waiting.load(Ordering::Acquire));
        *owner.lock().unwrap() = None;
        assert!(!second.activate().is_empty());
        assert!(!second.waiting.load(Ordering::Acquire));
    }
    #[test]
    fn camera_permission_disables_dynamic_factory() {
        let mut cfg = Config::default();
        cfg.redirect.camera = false;
        let factory = Factory {
            cfg,
            stats: Arc::new(PipeStats::default()),
        };
        let (tx, _) = tokio::sync::mpsc::unbounded_channel();
        let mut dvc = DrdynvcServer::new();
        factory.attach_dynamic(&mut dvc, tx);
        assert!(dvc.get_channel_id_by_type::<Enumerator>().is_none());
    }
    #[test]
    fn camera_enumeration_waits_for_client_version_selection() {
        let (sender, _) = tokio::sync::mpsc::unbounded_channel();
        let mut enumerator = Enumerator {
            cfg: Config::default(),
            stats: Arc::new(PipeStats::default()),
            sender,
            alive: Arc::new(AtomicBool::new(true)),
            version: None,
            devices: Arc::new(Mutex::new(BTreeMap::new())),
            total: 0,
            capture_owner: Arc::new(Mutex::new(None)),
        };
        assert!(enumerator.start(0).unwrap().is_empty());
        let responses = enumerator
            .process(
                0,
                &encode_vec(&EnumerationPdu::SelectVersionRequest(ProtocolVersion::V1)).unwrap(),
            )
            .unwrap();
        assert_eq!(
            decode::<EnumerationPdu>(&encode_vec(responses[0].as_ref()).unwrap()).unwrap(),
            EnumerationPdu::SelectVersionResponse(ProtocolVersion::V1)
        );
        assert_eq!(enumerator.version, Some(ProtocolVersion::V1));
    }
    #[test]
    fn camera_configuration_rejects_invalid_source_names_and_bounds() {
        for name in ["", "../camera", "bad,name", "with space", "camera\0"] {
            let mut cfg = Config::default();
            cfg.naming.camera_source_prefix = name.into();
            assert!(cfg.validate().is_err());
        }
        let mut cfg = Config::default();
        cfg.native.camera_max_devices = 0;
        assert!(cfg.validate().is_err());
        cfg.native.camera_max_devices = 4;
        cfg.native.camera_max_fps = 0;
        assert!(cfg.validate().is_err());
    }

    struct Synthetic {
        sink: Option<SampleSink>,
        count: u8,
        deny: bool,
        format: MediaFormat,
        encoder: Option<crate::encode::OpenH264>,
        jpegs: Vec<Vec<u8>>,
    }
    impl CameraBackend for Synthetic {
        fn activate(&mut self) -> Result<(), ErrorCode> {
            if self.deny {
                Err(ErrorCode::OperationNotSupported)
            } else {
                Ok(())
            }
        }
        fn deactivate(&mut self) -> Result<(), ErrorCode> {
            Ok(())
        }
        fn streams(&mut self) -> Result<Vec<StreamDescription>, ErrorCode> {
            Ok(vec![StreamDescription::color(true, true)])
        }
        fn media_types(&mut self, _: u8) -> Result<Vec<MediaType>, ErrorCode> {
            Ok(vec![media(self.format)])
        }
        fn current_media_type(&mut self, _: u8) -> Result<MediaType, ErrorCode> {
            Ok(media(self.format))
        }
        fn start_streams(
            &mut self,
            _: &[StartStreamInfo],
            sink: SampleSink,
        ) -> Result<(), ErrorCode> {
            if self.format == MediaFormat::H264 {
                self.encoder = Some(
                    crate::encode::OpenH264::new(
                        "auto",
                        crate::encode::EncoderSettings {
                            width: encoded_dimensions().0,
                            height: encoded_dimensions().1,
                            fps: 30.0,
                            bitrate_bps: 1000000,
                            max_bitrate_bps: 2000000,
                            qp_min: 18,
                            qp_max: 30,
                            threads: 1,
                            complexity: "low".into(),
                            full_range: true,
                        },
                    )
                    .unwrap(),
                );
            }
            self.sink = Some(sink);
            Ok(())
        }
        fn stop_streams(&mut self) -> Result<(), ErrorCode> {
            self.sink = None;
            Ok(())
        }
        fn sample_requested(&mut self, index: u8) {
            self.count = self.count.wrapping_add(1);
            let pixels = (encoded_dimensions().0 * encoded_dimensions().1) as usize;
            let mut frame = vec![0; pixels * 3 / 2];
            frame[..pixels].fill(32 + self.count % 160);
            frame[pixels..].fill(128);
            let packet = if let Some(encoder) = self.encoder.as_mut() {
                use crate::encode::H264Encoder;
                encoder.force_idr();
                encoder
                    .encode(
                        &crate::encode::I420 {
                            data: &frame,
                            width: encoded_dimensions().0,
                            height: encoded_dimensions().1,
                            stride: encoded_dimensions().0,
                        },
                        self.count as i64 * 33,
                    )
                    .unwrap()
                    .data
            } else if self.format == MediaFormat::Mjpg {
                self.jpegs[self.count as usize % self.jpegs.len()].clone()
            } else {
                frame
            };
            if let Some(sink) = self.sink.as_ref() {
                assert_eq!(sink.send_sample(index, packet), SampleDelivery::Sent);
            }
        }
        fn shutdown(&mut self) {
            self.sink = None;
        }
    }
    struct Wire {
        server: DrdynvcServer,
        client: DrdynvcClient,
        events: tokio::sync::mpsc::UnboundedReceiver<ServerEvent>,
        samples: mpsc::Receiver<Vec<SvcMessage>>,
        to_client: VecDeque<SvcMessage>,
        to_server: VecDeque<SvcMessage>,
    }
    impl Wire {
        fn new(stats: Arc<PipeStats>, enabled: bool, deny: bool) -> Self {
            let (sender, events) = tokio::sync::mpsc::unbounded_channel();
            let (sample_sender, samples) = mpsc::channel();
            let session = CameraRedirectionSession::new();
            let device = session
                .select_device(
                    DeviceDescriptor::new(
                        "Synthetic camera".into(),
                        "mios-test-camera-channel".into(),
                    )
                    .unwrap(),
                )
                .unwrap();
            let format = match std::env::var("MIOS_RDP_CAMERA_TEST_FORMAT").as_deref() {
                Ok("h264") => MediaFormat::H264,
                Ok("mjpg") => MediaFormat::Mjpg,
                _ => MediaFormat::I420,
            };
            let jpegs = if format == MediaFormat::Mjpg {
                jpeg_frames()
            } else {
                vec![]
            };
            let listener = device.listener(
                move || {
                    Some(Synthetic {
                        sink: None,
                        count: 0,
                        deny,
                        format,
                        encoder: None,
                        jpegs: jpegs.clone(),
                    })
                },
                Arc::new(move |_, messages| {
                    sample_sender
                        .send(messages)
                        .map_err(|_| pdu_other_err!("test uplink closed"))
                }),
            );
            let client = DrdynvcClient::new()
                .with_dynamic_channel(session.enumeration_client(vec![device]).unwrap())
                .with_listener(listener);
            let mut cfg = lab_config();
            cfg.redirect.camera = enabled;
            let mut server = DrdynvcServer::new();
            Factory { cfg, stats }.attach_dynamic(&mut server, sender);
            let to_client = server.start().unwrap().into();
            Self {
                server,
                client,
                events,
                samples,
                to_client,
                to_server: VecDeque::new(),
            }
        }
        fn camera_state(&self) -> Option<State> {
            let enum_id = self.server.get_channel_id_by_type::<Enumerator>()?;
            let enumerator = self.server.dvc_by_id::<Enumerator>(enum_id)?;
            let id = *enumerator
                .processor()
                .devices
                .lock()
                .unwrap()
                .values()
                .next()?;
            Some(self.server.dvc_by_id::<Camera>(id)?.processor().state)
        }
        fn pump(&mut self) {
            for _ in 0..1000 {
                let mut progress = false;
                if let Some(message) = self.to_client.pop_front() {
                    self.to_server.extend(
                        self.client
                            .process(&message.encode_unframed_pdu().unwrap())
                            .unwrap(),
                    );
                    progress = true;
                }
                if let Some(message) = self.to_server.pop_front() {
                    self.to_client.extend(
                        self.server
                            .process(&message.encode_unframed_pdu().unwrap())
                            .unwrap(),
                    );
                    progress = true;
                }
                while let Ok(messages) = self.samples.try_recv() {
                    self.to_server.extend(messages);
                    progress = true;
                }
                while let Ok(event) = self.events.try_recv() {
                    if let ServerEvent::DvcAction(action) = event {
                        self.to_client.extend(action(&mut self.server).unwrap());
                        progress = true;
                    }
                }
                if !progress {
                    break;
                }
            }
        }
    }
    fn lab_config() -> Config {
        let mut cfg = Config::default();
        cfg.native.camera_poll_ms = 250;
        cfg.native.camera_pipewire_runtime =
            std::env::var("MIOS_RDP_CAMERA_TEST_RUNTIME").unwrap_or_default();
        cfg.native.camera_v4l2 = std::env::var("MIOS_RDP_CAMERA_TEST_V4L2").as_deref() != Ok("0");
        if let Ok(library) = std::env::var("MIOS_RDP_CAMERA_TEST_V4L2_LIBRARY") {
            cfg.native.camera_v4l2_library = library;
        }
        if let Ok(restarts) = std::env::var("MIOS_RDP_CAMERA_TEST_PROVIDER_RESTARTS") {
            cfg.native.camera_provider_restarts = restarts.parse().unwrap();
        }
        cfg
    }
    fn lab_runtime() -> String {
        let runtime = std::env::var("MIOS_RDP_CAMERA_TEST_RUNTIME")
            .expect("MIOS_RDP_CAMERA_TEST_RUNTIME: a private lab PipeWire runtime");
        assert_ne!(
            std::path::Path::new(&runtime),
            std::path::Path::new(&format!("/run/user/{}", unsafe_uid())),
            "camera-role test nodes stay out of the operator's PipeWire graph"
        );
        assert!(std::path::Path::new(&runtime).join("pipewire-0").exists());
        assert_eq!(std::env::var("PIPEWIRE_RUNTIME_DIR").as_deref(), Ok(runtime.as_str()), "consumers inherit the lab PipeWire");
        runtime
    }
    /// argv prefix from a JSON array environment variable (lab wrappers).
    fn lab_command(var: &str) -> Option<Command> {
        let raw = std::env::var(var).ok()?;
        let argv: Vec<String> = serde_json::from_str(&raw).expect("JSON argv array");
        let mut cmd = Command::new(&argv[0]);
        cmd.args(&argv[1..]).env("PIPEWIRE_RUNTIME_DIR", lab_runtime());
        Some(cmd)
    }
    /// Run a consumer to completion while pumping the protocol loop.
    fn run_pumped(wire: &mut Wire, cmd: &mut Command, seconds: u64) -> Option<bool> {
        let mut child = cmd.stdout(Stdio::null()).spawn().unwrap();
        let deadline = Instant::now() + Duration::from_secs(seconds);
        loop {
            wire.pump();
            if let Some(status) = child.try_wait().unwrap() {
                return Some(status.success());
            }
            if Instant::now() > deadline {
                let _ = child.kill();
                let _ = child.wait();
                return None;
            }
            std::thread::sleep(Duration::from_millis(10));
        }
    }
    fn wait_for(wire: &mut Wire, seconds: u64, what: &str, mut done: impl FnMut(&mut Wire) -> bool) {
        let deadline = Instant::now() + Duration::from_secs(seconds);
        while !done(wire) {
            wire.pump();
            assert!(Instant::now() < deadline, "{what}");
            std::thread::sleep(Duration::from_millis(20));
        }
    }
    fn graph(runtime: &str) -> Vec<serde_json::Value> {
        let out = Command::new("pw-dump").env("PIPEWIRE_RUNTIME_DIR", runtime).output().unwrap();
        assert!(out.status.success());
        serde_json::from_slice::<serde_json::Value>(&out.stdout).unwrap().as_array().unwrap().clone()
    }
    /// Camera-role nodes as the portal/libwebrtc pickers filter them.
    fn picker_nodes(runtime: &str) -> Vec<serde_json::Value> {
        graph(runtime)
            .into_iter()
            .filter(|v| {
                v["type"] == "PipeWire:Interface:Node"
                    && v["info"]["props"]["media.class"] == "Video/Source"
                    && v["info"]["props"]["media.role"] == "Camera"
            })
            .map(|v| v["info"]["props"].clone())
            .collect()
    }
    /// The camera portal's IsCameraPresent, read through the lab's private bus.
    fn picker_probe(wire: &mut Wire) -> Option<bool> {
        let mut cmd = lab_command("MIOS_RDP_CAMERA_TEST_PICKER_CMD")?;
        let out = cmd.stderr(Stdio::inherit()).output().unwrap();
        wire.pump();
        let text = String::from_utf8_lossy(&out.stdout);
        Some(out.status.success() && text.split_whitespace().last() == Some("true"))
    }
    /// A V4L2-API consumer (ffmpeg -f v4l2) started through the lab's
    /// mios-camera-v4l2 launcher inside a private /dev without real devices.
    fn v4l2_consumer(output: &std::path::Path, frames: u32) -> Option<Command> {
        let mut cmd = lab_command("MIOS_RDP_CAMERA_TEST_V4L2_CMD")?;
        let (w, h) = dimensions();
        cmd.args([
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "v4l2",
            "-video_size", &format!("{w}x{h}"), "-i", "/dev/video0",
            "-frames:v", &frames.to_string(), "-f", "rawvideo", "-pix_fmt", "bgra", "-y",
        ])
        .arg(output);
        Some(cmd)
    }
    fn native_reader(name: &str, frames: u32) -> Command {
        let mut cmd = Command::new("gst-launch-1.0");
        // A converter gives pipewiresrc a concrete format to negotiate; straight into
        // fakesink the link never forms ("target not found") even with a camera.
        cmd.args(["-q", "pipewiresrc", &format!("target-object={name}"), &format!("num-buffers={frames}"), "!", "videoconvert", "!", "fakesink"]);
        cmd
    }
    #[allow(unsafe_code)]
    fn kill_own_child(pid: i32) {
        // kill(2) on this test's own provider child; no memory crosses FFI.
        assert_eq!(unsafe { libc::kill(pid, libc::SIGKILL) }, 0);
    }
    /// The PID of the gst-launch provider this test process spawned for `name`.
    fn provider_pid(runtime: &str, name: &str) -> i32 {
        let objects = graph(runtime);
        let node = objects
            .iter()
            .find(|v| v["type"] == "PipeWire:Interface:Node" && v["info"]["props"]["node.name"] == name)
            .expect("provider node");
        let client = &node["info"]["props"]["client.id"];
        let pid = objects
            .iter()
            .find(|v| v["type"] == "PipeWire:Interface:Client" && &v["id"] == client)
            .and_then(|c| c["info"]["props"]["application.process.id"].as_i64())
            .expect("provider client pid") as i32;
        assert_eq!(std::fs::read_to_string(format!("/proc/{pid}/comm")).unwrap().trim(), "gst-launch-1.0");
        let stat = std::fs::read_to_string(format!("/proc/{pid}/stat")).unwrap();
        let ppid: u32 = stat.rsplit(')').next().unwrap().split_whitespace().nth(1).unwrap().parse().unwrap();
        assert_eq!(ppid, std::process::id(), "only this test's own provider is signalled");
        pid
    }
    #[test]
    #[ignore = "operator opt-in: creates only synthetic per-test PipeWire nodes and a native consumer"]
    fn camera_drdynvc_to_native_video_consumer() {
        assert_eq!(std::env::var("MIOS_RDP_CAMERA_LIVE").as_deref(), Ok("1"));
        assert_ne!(unsafe_uid(), 0, "run as the desktop operator");
        let runtime = lab_runtime();
        let v4l2_enabled = lab_config().native.camera_v4l2;
        let stats = Arc::new(PipeStats::default());
        let mut wire = Wire::new(stats.clone(), true, false);
        let deadline = Instant::now() + Duration::from_secs(15);
        let node = loop {
            wire.pump();
            if let Some(node) = stats.camera_sources.lock().unwrap().first().cloned() {
                break node;
            }
            assert!(
                Instant::now() < deadline,
                "camera did not publish: {:?}",
                stats.camera_error.lock().unwrap()
            );
            std::thread::sleep(Duration::from_millis(10));
        };
        let output =
            std::env::temp_dir().join(format!("mios-camera-native-{}.i420", std::process::id()));
        let graph = Command::new("pw-dump").args(["-N",node["name"].as_str().unwrap()]).output().unwrap();
        assert!(graph.status.success());
        let graph: serde_json::Value = serde_json::from_slice(&graph.stdout).unwrap();
        assert!(graph.as_array().unwrap().iter().any(|n| n["info"]["props"]["node.name"] == node["name"] && n["info"]["params"]["EnumFormat"].as_array().is_some_and(|formats| formats.iter().any(|f| f["mediaType"] == "video" && f["format"] == "BGRA"))), "Camera must advertise the packed BGRA format used by OBS's safe single-plane path");
        let pickers = picker_nodes(&runtime);
        assert_eq!(pickers.len(), 1, "exactly the published camera is picker-visible");
        assert_eq!(pickers[0]["node.name"], node["name"]);
        assert_eq!(pickers[0]["node.description"], "Synthetic camera (MiOS RDP)");
        assert_eq!(node["native_format"], "BGRA");
        assert_eq!(node["consumers"]["v4l2"], v4l2_enabled && std::path::Path::new(&lab_config().native.camera_v4l2_library).is_file());
        let portal_sees_camera = picker_probe(&mut wire);
        assert_ne!(portal_sees_camera, Some(false), "camera portal must report a camera");
        assert!(!output.exists());
        let mut consumer = Command::new("gst-launch-1.0")
            .args([
                "-q",
                "pipewiresrc",
                &format!("target-object={}", node["name"].as_str().unwrap()),
                "num-buffers=12",
                "!",
                "videoconvert",
                "!",
                "video/x-raw,format=I420",
                "!",
                "filesink",
                &format!("location={}", output.display()),
            ])
            .stdout(Stdio::null())
            .spawn()
            .unwrap();
        let deadline = Instant::now() + Duration::from_secs(15);
        loop {
            wire.pump();
            if let Some(status) = consumer.try_wait().unwrap() {
                assert!(status.success());
                break;
            }
            if Instant::now() > deadline {
                let _ = consumer.kill();
                let _ = consumer.wait();
                panic!(
                    "native camera consumer timed out: {:?}",
                    stats.camera_error.lock().unwrap()
                );
            }
            std::thread::sleep(Duration::from_millis(10));
        }
        let bytes = std::fs::read(&output).unwrap();
        assert_eq!(bytes.len(), 320 * 240 * 3 / 2 * 12);
        let means: Vec<_> = bytes
            .chunks(320 * 240 * 3 / 2)
            .map(|f| f[..320 * 240].iter().map(|b| *b as u64).sum::<u64>() / (320 * 240))
            .collect();
        assert!(
            means.windows(2).any(|pair| pair[0] != pair[1])
                || bytes
                    .chunks(320 * 240 * 3 / 2)
                    .zip(bytes.chunks(320 * 240 * 3 / 2).skip(1))
                    .any(|(a, b)| a != b),
            "native consumer must see moving synthetic frames"
        );
        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            wire.pump();
            if wire.camera_state() == Some(State::Dormant) {
                break;
            }
            assert!(
                Instant::now() < deadline,
                "camera did not deactivate after consumer closed: {:?}",
                wire.camera_state()
            );
            std::thread::sleep(Duration::from_millis(10));
        }
        println!(
            "MIOS_CAMERA_REOPEN_STATE {}",
            json!({"initial":node,"current":stats.camera_sources.lock().unwrap().clone(),"workers":stats.camera_workers.load(Ordering::Acquire)})
        );
        let mut resumed = Command::new("gst-launch-1.0")
            .args([
                "-q",
                "pipewiresrc",
                &format!("target-object={}", node["name"].as_str().unwrap()),
                "num-buffers=6",
                "!",
                "videoconvert",
                "!",
                &format!(
                    "video/x-raw,format=I420,width={},height={}",
                    dimensions().0,
                    dimensions().1
                ),
                "!",
                "fakesink",
            ])
            .stdout(Stdio::null())
            .spawn()
            .unwrap();
        let deadline = Instant::now() + Duration::from_secs(10);
        loop {
            wire.pump();
            if let Some(status) = resumed.try_wait().unwrap() {
                assert!(status.success());
                break;
            }
            if Instant::now() > deadline {
                let _ = resumed.kill();
                let _ = resumed.wait();
                panic!(
                    "camera did not resume: {:?}",
                    stats.camera_error.lock().unwrap()
                );
            }
            std::thread::sleep(Duration::from_millis(10));
        }
        let mut obs_verified = false;
        if let Some(script) = std::env::var_os("MIOS_RDP_CAMERA_OBS_FIXTURE") {
            let (w,h) = dimensions();
            let mut obs = Command::new("python3").arg(script).args(["--consume",node["name"].as_str().unwrap(),"--format","BGRA","--width",&w.to_string(),"--height",&h.to_string()]).spawn().unwrap();
            let deadline = Instant::now() + Duration::from_secs(15);
            loop {
                wire.pump();
                if let Some(status) = obs.try_wait().unwrap() {
                    assert!(status.success(),"Installed OBS camera consumer failed: {status}");
                    obs_verified = true;
                    break;
                }
                if Instant::now() > deadline {
                    let _ = obs.kill();let _ = obs.wait();
                    panic!("Installed OBS camera consumer timed out");
                }
                std::thread::sleep(Duration::from_millis(10));
            }
        }
        let name = node["name"].as_str().unwrap().to_string();
        let mut v4l2 = json!(null);
        let v4l2_output =
            std::env::temp_dir().join(format!("mios-camera-v4l2-{}.bgra", std::process::id()));
        if let Some(mut cmd) = v4l2_consumer(&v4l2_output, 12) {
            assert!(!v4l2_output.exists());
            wait_for(&mut wire, 5, "camera must idle before the V4L2 consumer", |w| w.camera_state() == Some(State::Dormant));
            let ok = run_pumped(&mut wire, &mut cmd, 20);
            let (w, h) = dimensions();
            let frame = (w * h * 4) as usize;
            let data = std::fs::read(&v4l2_output).unwrap_or_default();
            let _ = std::fs::remove_file(&v4l2_output);
            if v4l2_enabled {
                assert_eq!(ok, Some(true), "V4L2-API consumer must read the camera");
                assert_eq!(data.len(), frame * 12);
                assert!(
                    data.chunks(frame).zip(data.chunks(frame).skip(1)).any(|(a, b)| a != b),
                    "V4L2 consumer must see moving synthetic frames"
                );
                let means: Vec<_> = data.chunks(frame).map(|f| f.iter().step_by(4).map(|b| *b as u64).sum::<u64>() / (w * h) as u64).collect();
                v4l2 = json!({"frames":12,"bytes":data.len(),"format":"BGRA","blue_means":means});
            } else {
                assert_ne!(ok, Some(true), "with camera_v4l2 off the launcher must not expose a V4L2 camera");
                assert!(data.is_empty(), "a V4L2 consumer without compatibility must receive no frames");
                v4l2 = json!({"frames":0,"refused":true,"reason":node["consumers"]["v4l2_reason"]});
            }
            // Either way the PipeWire camera keeps serving native consumers.
            assert_eq!(run_pumped(&mut wire, &mut native_reader(&name, 6), 15), Some(true), "{:?}", stats.camera_error.lock().unwrap());
        }
        let mut restart = json!(null);
        if std::env::var("MIOS_RDP_CAMERA_TEST_KILL_PROVIDER").as_deref() == Ok("1") {
            let allowed = lab_config().native.camera_provider_restarts;
            wait_for(&mut wire, 5, "camera must idle before the provider is killed", |w| w.camera_state() == Some(State::Dormant));
            let pid = provider_pid(&runtime, &name);
            kill_own_child(pid);
            if allowed > 0 {
                wait_for(&mut wire, 20, "the provider must restart and republish", |_| {
                    stats.camera_restarts.load(Ordering::Acquire) == 1
                        && stats.camera_sources.lock().unwrap().iter().any(|n| n["name"] == name.as_str())
                });
                wait_for(&mut wire, 10, "the restarted camera must idle", |w| w.camera_state() == Some(State::Dormant));
                assert_ne!(provider_pid(&runtime, &name), pid);
                assert_eq!(run_pumped(&mut wire, &mut native_reader(&name, 6), 15), Some(true), "camera must survive its provider exiting: {:?}", stats.camera_error.lock().unwrap());
                assert!(stats.camera_error.lock().unwrap().is_none());
                restart = json!({"provider_killed":true,"restarts":1,"native_after_restart":true});
            } else {
                wait_for(&mut wire, 10, "without restarts the camera closes", |_| stats.camera_workers.load(Ordering::Acquire) == 0);
                let error = stats.camera_error.lock().unwrap().take();
                assert!(error.as_deref().is_some_and(|e| e.contains(PROVIDER_EXITED)), "{error:?}");
                assert_eq!(stats.camera_restarts.load(Ordering::Acquire), 0);
                assert_ne!(run_pumped(&mut wire, &mut native_reader(&name, 1), 5), Some(true));
                restart = json!({"provider_killed":true,"restarts":0,"camera_closed":true,"error":error});
            }
        } else {
            assert_eq!(stats.camera_restarts.load(Ordering::Acquire), 0, "no provider restart on the healthy path");
        }
        let hot_remove = std::env::var("MIOS_RDP_CAMERA_TEST_REMOVE").as_deref() == Ok("1");
        if hot_remove {
            let messages = wire
                .client
                .get_dvc_mut::<ironrdp_rdpecam::EnumerationClient>()
                .unwrap()
                .processor_mut()
                .remove_device("mios-test-camera-channel")
                .unwrap();
            wire.to_server.extend(messages);
            wire.pump();
        }
        let enumeration_close =
            std::env::var("MIOS_RDP_CAMERA_TEST_REMOVE").as_deref() == Ok("enum");
        if enumeration_close {
            let id = wire.server.get_channel_id_by_type::<Enumerator>().unwrap();
            wire.to_server.extend(wire.client.close_channel(id));
            wire.pump();
        }
        if hot_remove || enumeration_close {
            let deadline = Instant::now() + Duration::from_secs(5);
            while stats.camera_workers.load(Ordering::Acquire) != 0 {
                wire.pump();
                assert!(
                    Instant::now() < deadline,
                    "camera must stop before the RDP connection is dropped"
                );
                std::thread::sleep(Duration::from_millis(10));
            }
            assert!(stats.camera_sources.lock().unwrap().is_empty());
        }
        drop(wire);
        let deadline = Instant::now() + Duration::from_secs(5);
        while stats.camera_workers.load(Ordering::Acquire) != 0 {
            assert!(Instant::now() < deadline);
            std::thread::sleep(Duration::from_millis(10));
        }
        assert!(stats.camera_sources.lock().unwrap().is_empty());
        let check = Command::new("pw-dump")
            .env("PIPEWIRE_RUNTIME_DIR", &runtime)
            .args(["-N", node["name"].as_str().unwrap()])
            .output()
            .unwrap();
        assert!(video_node(&check.stdout, node["name"].as_str().unwrap()).is_none());
        assert!(
            stats.camera_error.lock().unwrap().is_none(),
            "{:?}",
            stats.camera_error.lock().unwrap()
        );
        let mut negatives = vec![];
        for (enabled, deny) in [(false, false), (true, true)] {
            let negative = Arc::new(PipeStats::default());
            let mut wire = Wire::new(negative.clone(), enabled, deny);
            wire.pump();
            assert!(negative.camera_sources.lock().unwrap().is_empty());
            assert_eq!(negative.camera_workers.load(Ordering::Acquire), 0);
            assert!(picker_nodes(&runtime).is_empty(), "no picker-visible camera");
            let portal = picker_probe(&mut wire);
            assert_ne!(portal, Some(true), "camera portal must report no camera");
            let mut native = Command::new("gst-launch-1.0");
            // Same probe shape that reads a present camera (see native_reader), so a
            // refusal here means "no camera", not "probe cannot negotiate".
            native.args(["-q", "pipewiresrc", "autoconnect=true", "num-buffers=1", "!", "videoconvert", "!", "fakesink"]);
            let native_reads = run_pumped(&mut wire, &mut native, 4) == Some(true);
            assert!(!native_reads, "a native consumer must find no camera");
            let blank = std::env::temp_dir()
                .join(format!("mios-camera-v4l2-negative-{}.bgra", std::process::id()));
            let v4l2_reads = v4l2_consumer(&blank, 1).map(|mut cmd| {
                let ok = run_pumped(&mut wire, &mut cmd, 15);
                let bytes = std::fs::metadata(&blank).map(|m| m.len()).unwrap_or(0);
                let _ = std::fs::remove_file(&blank);
                ok == Some(true) && bytes > 0
            });
            assert_ne!(v4l2_reads, Some(true), "V4L2 consumer must see no camera");
            negatives.push(json!({"enabled":enabled,"client_denies":deny,"picker_nodes":0,"portal_camera":portal,"native_frames":0,"v4l2_frames":v4l2_reads.map(|_| 0)}));
        }
        println!(
            "MIOS_CAMERA_NATIVE_PROOF {}",
            json!({"frames":12,"bytes":bytes.len(),"luma_means":means,"source":node,"output":output,"advertised_video_format":true,"native_format":"BGRA","installed_obs_consumer":obs_verified,"picker_node":pickers[0],"portal_camera":portal_sees_camera,"v4l2":v4l2,"provider_restart":restart,"negatives":negatives,"consumer_dimensions_forced":false,"permission_controls":true,"consumer_idle_deactivates":true,"consumer_reopen_resumes":true,"hot_remove":hot_remove,"enumeration_close":enumeration_close,"disconnect_cleanup":true})
        );
    }
    fn jpeg_frames() -> Vec<Vec<u8>> {
        let path =
            std::env::temp_dir().join(format!("mios-camera-jpeg-{}.bin", std::process::id()));
        assert!(!path.exists());
        let status = Command::new("gst-launch-1.0")
            .args([
                "-q",
                "videotestsrc",
                "num-buffers=4",
                "pattern=ball",
                "!",
                "video/x-raw,width=320,height=240,framerate=30/1",
                "!",
                "jpegenc",
                "!",
                "filesink",
                &format!("location={}", path.display()),
            ])
            .status()
            .unwrap();
        assert!(status.success());
        let data = std::fs::read(&path).unwrap();
        std::fs::remove_file(path).unwrap();
        let mut result = vec![];
        let mut start = 0;
        for end in 1..data.len() {
            if data[end - 1..=end] == [0xff, 0xd9] {
                result.push(data[start..=end].to_vec());
                start = end + 1;
            }
        }
        assert_eq!(result.len(), 4);
        assert!(result.iter().all(|p| p.starts_with(&[0xff, 0xd8])));
        result
    }
    #[allow(unsafe_code)]
    fn unsafe_uid() -> u32 {
        unsafe { libc::geteuid() }
    }
}
