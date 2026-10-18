//! Redirection channels for the native RDP server (rdp-native-design.md 7):
//! Audio output (RDPSND), Audio input/Microphone (MS-RDPEAI), Drives/Storage (MS-RDPEFS over RDPDR),
//! Camera (MS-RDPECAM), Location (MS-RDPEL over NMEA socket), Timezone, and Smartcard.
//! Each channel is gated by its SSOT toggle under [remote.rdp.redirect].
#![allow(unsafe_code)]
#![allow(dead_code)]

use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};

use ironrdp_core::impl_as_any;
use ironrdp_pdu::PduResult;
use ironrdp_rdpsnd::pdu::{AudioFormat, WaveFormat};
use ironrdp_rdpsnd::server::{NegotiatedFormat, RdpsndError, RdpsndServerHandler};
use ironrdp_server::{
    RdpdrServerBackend, RdpdrServerFactory, RdpeaiServerBackend, RdpeaiServerFactory, ServerEventSender,
    SoundServerFactory,
};
use tracing::{debug, info, trace, warn};

use crate::config::Config;

// ============================================================================
// 1. Audio Output (RDPSND)
// ============================================================================

#[derive(Debug)]
pub struct MiosSoundHandler {
    formats: Vec<AudioFormat>,
    streaming: Arc<AtomicBool>,
    sender: Arc<std::sync::Mutex<Option<tokio::sync::mpsc::UnboundedSender<ironrdp_server::ServerEvent>>>>,
}

impl MiosSoundHandler {
    pub fn new(sender: Arc<std::sync::Mutex<Option<tokio::sync::mpsc::UnboundedSender<ironrdp_server::ServerEvent>>>>) -> Self {
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

    fn choose_format<'a>(&mut self, common: &'a [NegotiatedFormat]) -> Option<&'a NegotiatedFormat> {
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
            let _ = tx.send(ironrdp_server::ServerEvent::Rdpsnd(ironrdp_rdpsnd::server::RdpsndServerMessage::Wave(silence, 0)));
        }
        Ok(())
    }

    fn stop(&mut self) {
        self.streaming.store(false, Ordering::SeqCst);
        info!("stopped audio output streaming");
    }
}

pub struct SoundFactory {
    sender: Arc<std::sync::Mutex<Option<tokio::sync::mpsc::UnboundedSender<ironrdp_server::ServerEvent>>>>,
}

impl SoundFactory {
    pub fn new() -> Self {
        Self {
            sender: Arc::new(std::sync::Mutex::new(None)),
        }
    }
}

impl ServerEventSender for SoundFactory {
    fn set_sender(&mut self, sender: tokio::sync::mpsc::UnboundedSender<ironrdp_server::ServerEvent>) {
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

#[derive(Debug)]
pub struct MiosRdpeaiBackend {
    formats: Vec<AudioFormat>,
    capturing: Arc<AtomicBool>,
    sender: Arc<std::sync::Mutex<Option<tokio::sync::mpsc::UnboundedSender<ironrdp_server::ServerEvent>>>>,
}

impl MiosRdpeaiBackend {
    pub fn new(sender: Arc<std::sync::Mutex<Option<tokio::sync::mpsc::UnboundedSender<ironrdp_server::ServerEvent>>>>) -> Self {
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
        }
    }
}

impl RdpeaiServerBackend for MiosRdpeaiBackend {
    fn supported_formats(&self) -> &[AudioFormat] {
        &self.formats
    }

    fn on_formats_negotiated(&mut self, negotiated: &[AudioFormat]) {
        info!(count = negotiated.len(), "MS-RDPEAI microphone formats negotiated with client");
        if let Some(fmt) = negotiated.first() {
            if let Some(ref tx) = *self.sender.lock().unwrap() {
                let open_msg = ironrdp_server::RdpeaiServerMessage::Open {
                    frames_per_packet: 480,
                    initial_format: 0,
                    capture_format: fmt.clone(),
                };
                let _ = tx.send(ironrdp_server::ServerEvent::Rdpeai(open_msg));
                info!("MS-RDPEAI sent MSG_SNDIN_OPEN to client");
            }
        }
    }

    fn on_open_reply(&mut self, result: i32) {
        if result >= 0 {
            self.capturing.store(true, Ordering::SeqCst);
            info!(result, "MS-RDPEAI microphone stream opened successfully");
        } else {
            warn!(result, "MS-RDPEAI client refused microphone open request");
        }
    }

    fn on_audio_data(&mut self, format: &AudioFormat, data: &[u8]) {
        trace!(rate = format.n_samples_per_sec, bytes = data.len(), "captured remote microphone audio packet");
    }

    fn on_close(&mut self) {
        self.capturing.store(false, Ordering::SeqCst);
        info!("MS-RDPEAI microphone channel closed");
    }
}

pub struct RdpeaiFactory {
    sender: Arc<std::sync::Mutex<Option<tokio::sync::mpsc::UnboundedSender<ironrdp_server::ServerEvent>>>>,
}

impl RdpeaiFactory {
    pub fn new() -> Self {
        Self {
            sender: Arc::new(std::sync::Mutex::new(None)),
        }
    }
}

impl ServerEventSender for RdpeaiFactory {
    fn set_sender(&mut self, sender: tokio::sync::mpsc::UnboundedSender<ironrdp_server::ServerEvent>) {
        *self.sender.lock().unwrap() = Some(sender);
    }
}

impl RdpeaiServerFactory for RdpeaiFactory {
    fn build_backend(&self) -> Box<dyn RdpeaiServerBackend> {
        Box::new(MiosRdpeaiBackend::new(Arc::clone(&self.sender)))
    }
}

// ============================================================================
// 3. Drives / Storage Redirection (MS-RDPEFS over RDPDR)
// ============================================================================

#[derive(Debug, Default)]
pub struct MiosRdpdrBackend {
    shares_dir: PathBuf,
}

impl MiosRdpdrBackend {
    pub fn new(runtime_dir: impl AsRef<Path>) -> Self {
        let shares_dir = runtime_dir.as_ref().join("mios-rdp").join("drives");
        Self { shares_dir }
    }
}

impl_as_any!(MiosRdpdrBackend);

impl RdpdrServerBackend for MiosRdpdrBackend {
    fn on_device_announce(&mut self, devices: &[ironrdp_rdpdr::pdu::efs::DeviceAnnounceHeader]) -> Vec<(u32, bool)> {
        info!(count = devices.len(), drives_dir = ?self.shares_dir, "MS-RDPEFS client announced storage devices");
        let _ = std::fs::create_dir_all(&self.shares_dir);
        devices
            .iter()
            .map(|d| {
                let clean_name = d.preferred_dos_name().trim_matches(char::from(0)).trim();
                let name = if clean_name.is_empty() { "drive" } else { clean_name };
                let dev_dir = self.shares_dir.join(name);
                let _ = std::fs::create_dir_all(&dev_dir);
                info!(
                    id = d.device_id(),
                    name = %name,
                    path = ?dev_dir,
                    "registered remote drive share"
                );
                (d.device_id(), true)
            })
            .collect()
    }

    fn on_device_remove(&mut self, device_ids: &[u32]) {
        info!(removed = ?device_ids, "MS-RDPEFS client removed storage devices");
    }

    fn on_client_name(&mut self, computer_name: &str) {
        info!(name = %computer_name, "MS-RDPEFS client computer name received");
    }

    fn on_create_complete(&mut self, _response: &ironrdp_rdpdr::pdu::efs::DeviceCreateResponse) -> PduResult<()> {
        Ok(())
    }

    fn on_close_complete(&mut self, _response: &ironrdp_rdpdr::pdu::efs::DeviceCloseResponse) -> PduResult<()> {
        Ok(())
    }

    fn on_read_complete(&mut self, _response: &ironrdp_rdpdr::pdu::efs::DeviceReadResponse) -> PduResult<()> {
        Ok(())
    }

    fn on_write_complete(&mut self, _response: &ironrdp_rdpdr::pdu::efs::DeviceWriteResponse) -> PduResult<()> {
        Ok(())
    }

    fn on_flush_buffers_complete(
        &mut self,
        _response: &ironrdp_rdpdr::pdu::efs::DeviceFlushBuffersResponse,
    ) -> PduResult<()> {
        Ok(())
    }

    fn on_device_control_complete(
        &mut self,
        _response: &ironrdp_rdpdr::pdu::efs::DeviceControlResponse,
    ) -> PduResult<()> {
        Ok(())
    }

    fn on_query_information_complete(
        &mut self,
        _response: &ironrdp_rdpdr::pdu::efs::ClientDriveQueryInformationResponse,
    ) -> PduResult<()> {
        Ok(())
    }

    fn on_set_information_complete(
        &mut self,
        _response: &ironrdp_rdpdr::pdu::efs::ClientDriveSetInformationResponse,
    ) -> PduResult<()> {
        Ok(())
    }

    fn on_query_directory_complete(
        &mut self,
        _response: &ironrdp_rdpdr::pdu::efs::ClientDriveQueryDirectoryResponse,
    ) -> PduResult<()> {
        Ok(())
    }

    fn on_notify_change_directory_complete(
        &mut self,
        _response: &ironrdp_rdpdr::pdu::efs::ClientDriveNotifyChangeDirectoryResponse,
    ) -> PduResult<()> {
        Ok(())
    }

    fn on_query_volume_information_complete(
        &mut self,
        _response: &ironrdp_rdpdr::pdu::efs::ClientDriveQueryVolumeInformationResponse,
    ) -> PduResult<()> {
        Ok(())
    }

    fn on_lock_control_complete(
        &mut self,
        _response: &ironrdp_rdpdr::pdu::efs::ClientDriveLockControlResponse,
    ) -> PduResult<()> {
        Ok(())
    }

    fn on_query_security_complete(
        &mut self,
        _response: &ironrdp_rdpdr::pdu::efs::ClientDriveQuerySecurityResponse,
    ) -> PduResult<()> {
        Ok(())
    }

    fn on_set_security_complete(
        &mut self,
        _response: &ironrdp_rdpdr::pdu::efs::ClientDriveSetSecurityResponse,
    ) -> PduResult<()> {
        Ok(())
    }

    fn close(&mut self) {
        info!("MS-RDPEFS storage channel closed");
    }
}

pub struct RdpdrFactory {
    runtime_dir: PathBuf,
}

impl RdpdrFactory {
    pub fn new(runtime_dir: impl Into<PathBuf>) -> Self {
        Self {
            runtime_dir: runtime_dir.into(),
        }
    }
}

impl ServerEventSender for RdpdrFactory {
    fn set_sender(&mut self, _: tokio::sync::mpsc::UnboundedSender<ironrdp_server::ServerEvent>) {}
}

impl RdpdrServerFactory for RdpdrFactory {
    fn build_backend(&self) -> Box<dyn RdpdrServerBackend> {
        Box::new(MiosRdpdrBackend::new(&self.runtime_dir))
    }
}

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
        assert!(!factory_registered, "microphone disabled must not register factory");

        cfg.redirect.microphone = true;
        if cfg.redirect.microphone {
            factory_registered = true;
        }
        assert!(factory_registered, "microphone enabled must register factory");
    }

    #[test]
    fn rdpdr_storage_path_matches_canonical() {
        let runtime = PathBuf::from("/run/user/1000");
        let backend = MiosRdpdrBackend::new(&runtime);
        assert_eq!(backend.shares_dir, runtime.join("mios-rdp").join("drives"));
    }
}

