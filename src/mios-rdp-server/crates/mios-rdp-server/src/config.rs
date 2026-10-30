//! native.json, written by `mios-rdp native` from the SSOT ([remote.rdp], [remote.rdp.native],
//! [remote.rdp.redirect], [remote.rdp.naming]) plus runtime facts. Every default here equals the
//! SSOT value (rdp-native-design.md 9); a missing key never changes behaviour.
#![forbid(unsafe_code)]

use std::net::IpAddr;
use std::path::{Path, PathBuf};

use anyhow::{Context as _, Result, bail};
use serde::Deserialize;

fn d_true() -> bool {
    true
}

#[derive(Debug, Clone, Deserialize, serde::Serialize)]
#[serde(default)]
pub struct Native {
    pub codec: String,
    pub encoder: String,
    pub openh264_library: String,
    pub encoder_threads: u16,
    pub encoder_complexity: String,
    pub initial_bitrate_kbps: u32,
    pub min_bitrate_kbps: u32,
    pub max_bitrate_kbps: u32,
    pub qp_min: i32,
    pub qp_max: i32,
    pub refine_static: bool,
    pub frames_in_flight: u32,
    pub cursor: String,
    pub color_matrix: String,
    pub capture_modifiers: String,
    pub capture_path: String,
    pub convert_threads: usize,
    pub gpu_priority: String,
    pub gpu_guard: bool,
    pub gpu_guard_min_hz: u32,
    pub auto_reconnect: bool,
    pub auto_reconnect_seconds: u64,
    pub auth_backoff_seconds: u64,
    pub keepalive_seconds: u64,
    pub tcp_send_buffer_kib: u32,
    pub tcp_notsent_lowat_kib: u32,
    pub udp_transport: bool,
    pub log_negotiation: bool,
    pub bitmap_hz: u32,
    pub progressive: bool,
    pub progressive_hz: u32,
    pub progressive_quant: u8,
    pub audio_control_program: String,
    pub microphone_queue_packets: usize,
    pub drive_timeout_ms: u64,
    pub drive_max_devices: usize,
    pub drive_max_entries: usize,
    pub drive_io_kib: u32,
    pub bitmap_damage_threshold: u8,
    pub bitmap_slow_queue_ms: f64,
    pub bitmap_min_hz: u32,
    pub slow_ack_ms: f64,
    pub egfx_wait_ms: u64,
    pub max_width: u16,
    pub max_height: u16,
}

impl Default for Native {
    fn default() -> Self {
        Self {
            codec: "avc420".into(),
            encoder: "openh264".into(),
            openh264_library: "auto".into(),
            encoder_threads: 4,
            encoder_complexity: "low".into(),
            initial_bitrate_kbps: 8000,
            min_bitrate_kbps: 1500,
            max_bitrate_kbps: 25000,
            qp_min: 18,
            qp_max: 40,
            refine_static: true,
            frames_in_flight: 3,
            cursor: "composited".into(),
            color_matrix: "bt709-full".into(),
            capture_modifiers: "auto".into(),
            capture_path: "auto".into(),
            convert_threads: 4,
            gpu_priority: "low".into(),
            gpu_guard: false,
            gpu_guard_min_hz: 60,
            auto_reconnect: true,
            auto_reconnect_seconds: 300,
            auth_backoff_seconds: 2,
            keepalive_seconds: 5,
            tcp_send_buffer_kib: 128,
            tcp_notsent_lowat_kib: 16,
            udp_transport: false,
            log_negotiation: true,
            bitmap_hz: 15,
            progressive: true,
            progressive_hz: 0,
            progressive_quant: 8,
            audio_control_program: "pactl".into(),
            microphone_queue_packets: 8,
            drive_timeout_ms: 10000,
            drive_max_devices: 16,
            drive_max_entries: 4096,
            drive_io_kib: 256,
            bitmap_damage_threshold: 3,
            bitmap_slow_queue_ms: 250.0,
            bitmap_min_hz: 2,
            slow_ack_ms: 250.0,
            egfx_wait_ms: 3000,
            max_width: 4096,
            max_height: 4096,
        }
    }
}

impl Native {
    /// Zero inherits the operator's remote display target instead of imposing
    /// a separate codec cap. A nonzero value is an explicit lower ceiling.
    pub fn progressive_ceiling(&self, target: u32) -> u32 {
        if self.progressive_hz == 0 { target.max(1) } else { self.progressive_hz.min(target).max(1) }
    }
}

#[derive(Debug, Clone, Deserialize, serde::Serialize)]
#[serde(default)]
pub struct Redirect {
    pub clipboard: String,
    pub clipboard_max_mb: u64,
    pub touch: bool,
    pub pen: bool,
    pub audio_output: bool,
    pub microphone: bool,
    pub camera: bool,
    pub drives: bool,
    pub location: bool,
    pub timezone: String,
    pub smartcard: bool,
}

impl Default for Redirect {
    fn default() -> Self {
        Self {
            clipboard: "both".into(),
            clipboard_max_mb: 64,
            touch: true,
            pen: true,
            audio_output: false,
            microphone: true,
            camera: false,
            drives: true,
            location: false,
            timezone: "report".into(),
            smartcard: false,
        }
    }
}

#[derive(Debug, Clone, Deserialize, serde::Serialize)]
#[serde(default)]
pub struct Naming {
    pub touch_device: String,
    pub pen_device: String,
    pub microphone_source_prefix: String,
}

impl Default for Naming {
    fn default() -> Self {
        Self {
            touch_device: "MiOS Remote Touch".into(),
            pen_device: "MiOS Remote Pen".into(),
            microphone_source_prefix: "mios-remote-microphone".into(),
        }
    }
}

#[derive(Debug, Clone, Deserialize)]
#[serde(default)]
pub struct Config {
    pub listen: String,
    pub operator_uid: u32,
    pub runtime_dir: PathBuf,
    pub stats_path: PathBuf,
    pub tailscale_cidrs: Vec<String>,
    pub allow_lan: bool,
    pub lan_cidrs: Vec<String>,
    pub allow_loopback: bool,
    pub bind_device: Option<String>,
    pub tailscale_interface: String,
    pub tls_protocols: String,
    /// "priv" = cert/key fds from mios-rdp-priv; otherwise read cert_file/key_file directly (tests)
    pub tls_source: String,
    pub cert_file: PathBuf,
    pub key_file: PathBuf,
    /// "priv" = PAM through mios-rdp-priv; "test" only in test-auth builds
    pub auth: String,
    pub priv_socket: PathBuf,
    pub test_user: String,
    pub test_password_file: PathBuf,
    /// test-auth builds only: JSON lines of synthetic RDPEI touch/pen frames (tests T1/T2)
    pub test_input_fifo: PathBuf,
    pub mode: String,
    pub virtual_output: String,
    pub target_hz: u32,
    pub min_hz: u32,
    pub refresh_pacing: String,
    pub keyboard_layout: String,
    pub display_service: Vec<String>,
    pub display_config: PathBuf,
    pub instance_label: String,
    #[serde(default = "d_true")]
    pub input: bool,
    pub native: Native,
    pub redirect: Redirect,
    pub naming: Naming,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            listen: "0.0.0.0:8300".into(),
            operator_uid: 1000,
            runtime_dir: PathBuf::from("/run/user/1000"),
            stats_path: PathBuf::from("/run/user/1000/mios-rdp/stats.json"),
            tailscale_cidrs: vec!["100.64.0.0/10".into(), "fd7a:115c:a1e0::/48".into()],
            allow_lan: false,
            lan_cidrs: vec![],
            allow_loopback: false,
            bind_device: None,
            tailscale_interface: "tailscale0".into(),
            tls_protocols: "TLSv1.2, TLSv1.3".into(),
            tls_source: "priv".into(),
            cert_file: PathBuf::from("/var/lib/mios-rdp/cert.pem"),
            key_file: PathBuf::from("/var/lib/mios-rdp/key.pem"),
            auth: "priv".into(),
            priv_socket: PathBuf::from("/run/mios-rdp-priv.sock"),
            test_user: String::new(),
            test_password_file: PathBuf::new(),
            test_input_fifo: PathBuf::new(),
            mode: "virtual".into(),
            virtual_output: "HEADLESS-MIOS-RDP".into(),
            target_hz: 90,
            min_hz: 30,
            refresh_pacing: "adaptive".into(),
            keyboard_layout: "us".into(),
            display_service: vec![
                "python3".into(),
                "-I".into(),
                "/usr/libexec/mios/mios-rdp-rfb-bridge".into(),
                "--display-service".into(),
            ],
            display_config: PathBuf::new(),
            instance_label: String::new(),
            input: true,
            native: Native::default(),
            redirect: Redirect::default(),
            naming: Naming::default(),
        }
    }
}

impl Config {
    pub fn load(path: &Path) -> Result<Self> {
        let raw = std::fs::read_to_string(path).with_context(|| format!("read {}", path.display()))?;
        let mut c: Config = serde_json::from_str(&raw).with_context(|| format!("parse {}", path.display()))?;
        if c.display_config.as_os_str().is_empty() {
            c.display_config = path.to_path_buf();
        }
        c.validate()?;
        Ok(c)
    }

    pub fn validate(&mut self) -> Result<()> {
        if !(100..=60000).contains(&self.native.drive_timeout_ms) || !(1..=64).contains(&self.native.drive_max_devices)
            || !(64..=65536).contains(&self.native.drive_max_entries) || !(4..=1024).contains(&self.native.drive_io_kib)
            || self.native.drive_io_kib % 4 != 0 {
            bail!("drive limits: timeout_ms 100..60000, devices 1..64, entries 64..65536, IO 4..1024 KiB aligned to 4 KiB");
        }
        let prefix = &self.naming.microphone_source_prefix;
        if prefix.is_empty() || prefix.len() > 64 || !prefix.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_') {
            bail!("microphone source prefix must contain 1..=64 ASCII letters, digits, hyphens or underscores");
        }
        if self.native.audio_control_program.is_empty() || self.native.audio_control_program.contains('\0') || !(1..=128).contains(&self.native.microphone_queue_packets) {
            bail!("audio_control_program must be nonempty and microphone_queue_packets must be 1..=128");
        }
        if !(1..=240).contains(&self.target_hz) {
            bail!("target_hz {} out of 1..240", self.target_hz);
        }
        self.min_hz = self.min_hz.clamp(1, self.target_hz);
        if self.mode != "virtual" && self.mode != "mirror" {
            bail!("mode {} (virtual|mirror)", self.mode);
        }
        if self.mode == "virtual" && !self.virtual_output.starts_with("HEADLESS-") {
            bail!("virtual_output must start with HEADLESS-");
        }
        let n = &mut self.native;
        if n.codec != "avc420" && n.codec != "auto" {
            tracing::warn!("[remote.rdp.native].codec {} not built (Phase 8); using avc420", n.codec);
            n.codec = "avc420".into();
        }
        if n.encoder != "openh264" && n.encoder != "auto" {
            tracing::warn!("[remote.rdp.native].encoder {}: only Cisco OpenH264 is built (operator decision 2026-10-17)", n.encoder);
        }
        n.encoder = "openh264".into();
        n.min_bitrate_kbps = n.min_bitrate_kbps.max(100);
        n.max_bitrate_kbps = n.max_bitrate_kbps.max(n.min_bitrate_kbps);
        n.initial_bitrate_kbps = n.initial_bitrate_kbps.clamp(n.min_bitrate_kbps, n.max_bitrate_kbps);
        n.qp_min = n.qp_min.clamp(0, 51);
        n.qp_max = n.qp_max.clamp(n.qp_min, 51);
        n.frames_in_flight = n.frames_in_flight.clamp(1, 16);
        n.bitmap_min_hz = n.bitmap_min_hz.clamp(1, n.bitmap_hz.max(1));
        if n.progressive_hz > 240 || !(6..=15).contains(&n.progressive_quant) {
            bail!("progressive_hz must be 0..=240 and progressive_quant must be 6..=15");
        }
        if n.bitmap_damage_threshold > 16 { bail!("bitmap_damage_threshold must be 0..=16"); }
        if n.tcp_send_buffer_kib > 4096 || n.tcp_notsent_lowat_kib > 4096 {
            bail!("tcp_send_buffer_kib and tcp_notsent_lowat_kib must be 0..=4096");
        }
        n.encoder_threads = n.encoder_threads.clamp(1, 8);
        n.convert_threads = n.convert_threads.clamp(1, 8);
        if !matches!(n.gpu_priority.as_str(), "low" | "normal") {
            bail!("gpu_priority {} (low|normal)", n.gpu_priority);
        }
        if !matches!(n.capture_path.as_str(), "auto" | "cpu" | "gpu") {
            bail!("capture_path {} (auto|cpu|gpu)", n.capture_path);
        }
        if crate::gpu::Matrix::parse(&n.color_matrix).is_none() {
            bail!("color_matrix {} (bt709-full|bt709-limited|bt601-limited)", n.color_matrix);
        }
        if !matches!(self.redirect.clipboard.as_str(), "both" | "to-client" | "to-host" | "off") {
            bail!("[remote.rdp.redirect].clipboard {} (both|to-client|to-host|off)", self.redirect.clipboard);
        }
        Ok(())
    }

    /// Source addresses admitted before a byte is parsed: tailnet, LAN when allow_lan, loopback for
    /// private test instances.
    pub fn peer_allowed(&self, ip: IpAddr) -> bool {
        let ip = match ip {
            IpAddr::V6(v6) => v6.to_ipv4_mapped().map_or(IpAddr::V6(v6), IpAddr::V4),
            v4 => v4,
        };
        if ip.is_loopback() {
            return self.allow_loopback;
        }
        let lists: Vec<&String> = if self.allow_lan {
            self.tailscale_cidrs.iter().chain(self.lan_cidrs.iter()).collect()
        } else {
            self.tailscale_cidrs.iter().collect()
        };
        lists.into_iter().any(|c| cidr_contains(c, ip))
    }
}

pub fn cidr_contains(cidr: &str, ip: IpAddr) -> bool {
    let (net, bits) = match cidr.split_once('/') {
        Some((n, b)) => (n, b.parse::<u32>().ok()),
        None => (cidr, None),
    };
    let Ok(net) = net.parse::<IpAddr>() else { return false };
    match (net, ip) {
        (IpAddr::V4(n), IpAddr::V4(a)) => {
            let b = bits.unwrap_or(32).min(32);
            let m = if b == 0 { 0 } else { u32::MAX << (32 - b) };
            (u32::from(n) & m) == (u32::from(a) & m)
        }
        (IpAddr::V6(n), IpAddr::V6(a)) => {
            let b = bits.unwrap_or(128).min(128);
            let m = if b == 0 { 0 } else { u128::MAX << (128 - b) };
            (u128::from(n) & m) == (u128::from(a) & m)
        }
        _ => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tailnet_only_by_default() {
        let c = Config::default();
        assert!(c.peer_allowed("100.101.102.103".parse().unwrap()));
        assert!(c.peer_allowed("fd7a:115c:a1e0::1234".parse().unwrap()));
        assert!(!c.peer_allowed("192.168.1.20".parse().unwrap()));
        assert!(!c.peer_allowed("127.0.0.1".parse().unwrap()));
        assert!(!c.peer_allowed("8.8.8.8".parse().unwrap()));
        assert!(!c.peer_allowed("::ffff:8.8.8.8".parse().unwrap()));
        assert!(c.peer_allowed("::ffff:100.64.0.1".parse().unwrap()));
    }

    #[test]
    fn loopback_and_lan_are_opt_in() {
        let mut c = Config { allow_loopback: true, ..Config::default() };
        assert!(c.peer_allowed("127.0.0.1".parse().unwrap()));
        assert!(c.peer_allowed("::1".parse().unwrap()));
        c.lan_cidrs = vec!["192.168.0.0/16".into()];
        assert!(!c.peer_allowed("192.168.1.20".parse().unwrap()));
        c.allow_lan = true;
        assert!(c.peer_allowed("192.168.1.20".parse().unwrap()));
    }

    #[test]
    fn defaults_validate() {
        let mut c = Config::default();
        c.validate().unwrap();
        assert_eq!(c.native.encoder, "openh264");
        let mut bad = Config { mode: "x".into(), ..Config::default() };
        assert!(bad.validate().is_err());
    }

    #[test]
    fn microphone_endpoint_settings_are_bounded() {
        for packets in [1, 8, 128] {
            let mut cfg = Config::default(); cfg.native.microphone_queue_packets = packets;
            cfg.validate().unwrap();
        }
        for packets in [0, 129] {
            let mut cfg = Config::default(); cfg.native.microphone_queue_packets = packets;
            assert!(cfg.validate().unwrap_err().to_string().contains("microphone_queue_packets"));
        }
        for name in ["", "../audio", "source name", "source\0"] {
            let mut cfg = Config::default(); cfg.naming.microphone_source_prefix = name.into();
            assert!(cfg.validate().unwrap_err().to_string().contains("microphone source prefix"));
        }
        let mut cfg = Config::default(); cfg.native.audio_control_program.clear();
        assert!(cfg.validate().is_err());
    }
    #[test]
    fn native_drive_limits_reject_unbounded_or_unaligned_values() {
        let mut cfg=Config::default();cfg.validate().unwrap();
        cfg.native.drive_io_kib=17;assert!(cfg.validate().unwrap_err().to_string().contains("drive limits"));
        cfg.native.drive_io_kib=16;cfg.native.drive_timeout_ms=99;assert!(cfg.validate().is_err());
        cfg.native.drive_timeout_ms=100;cfg.native.drive_max_devices=65;assert!(cfg.validate().is_err());
        cfg.native.drive_max_devices=16;cfg.native.drive_max_entries=63;assert!(cfg.validate().is_err());
    }

    #[test]
    fn bitmap_noise_threshold_has_an_exact_mode_and_bounded_loss() {
        for threshold in [0,3,16] {
            let mut c=Config::default();c.native.bitmap_damage_threshold=threshold;
            c.validate().unwrap();assert_eq!(c.native.bitmap_damage_threshold,threshold);
        }
        let mut bad=Config::default();bad.native.bitmap_damage_threshold=17;
        assert!(bad.validate().unwrap_err().to_string().contains("bitmap_damage_threshold"));
    }

    #[test]
    fn tcp_queue_settings_allow_defaults_and_reject_excessive_buffers() {
        for value in [0, 16, 128, 4096] {
            let mut c=Config::default();
            c.native.tcp_send_buffer_kib=value; c.native.tcp_notsent_lowat_kib=value;
            c.validate().unwrap();
        }
        for send_buffer in [true, false] {
            let mut c=Config::default();
            if send_buffer { c.native.tcp_send_buffer_kib=4097; }
            else { c.native.tcp_notsent_lowat_kib=4097; }
            assert!(c.validate().unwrap_err().to_string().contains("tcp_"));
        }
    }

    #[test]
    fn bind_device_defaults() {
        let c = Config::default();
        assert_eq!(c.bind_device, None);
        assert_eq!(c.tailscale_interface, "tailscale0");
    }

    #[test]
    fn progressive_quality_and_rate_are_bounded_and_configurable() {
        for (hz, quant) in [(0,8), (1,6), (30,8), (240,15)] {
            let mut c=Config::default();
            c.native.progressive_hz=hz; c.native.progressive_quant=quant;
            c.validate().unwrap();
            assert_eq!(c.native.progressive_hz,hz); assert_eq!(c.native.progressive_quant,quant);
        }
        for (hz, quant) in [(241,8), (30,5), (30,16)] {
            let mut c=Config::default();
            c.native.progressive_hz=hz; c.native.progressive_quant=quant;
            assert!(c.validate().unwrap_err().to_string().contains("progressive_"));
        }
        let mut n=Native::default();
        assert_eq!(n.progressive_ceiling(90),90);
        assert_eq!(n.progressive_ceiling(144),144);
        n.progressive_hz=30;
        assert_eq!(n.progressive_ceiling(90),30);
        assert_eq!(n.progressive_ceiling(15),15);
    }
}
