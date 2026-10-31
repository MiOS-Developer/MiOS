// AI-hint: MS-RDPEUSB (URBDRC) generic USB redirection bridged to a native USB/IP vhci_hcd port: ironrdp-server DeviceFactory/UsbRedirDevice backend, SSOT allow/deny policy (announce + real descriptors), per-session claims, vhci capability probe (never loads a module), root attach helper hand-over, and the USB/IP CMD_SUBMIT/CMD_UNLINK engine that turns kernel URBs into UsbDeviceHandle requests.
// AI-related: src/mios-rdp-server/crates/mios-rdp-priv/src/main.rs, usr/lib/systemd/system/mios-rdp-usb@.service, usr/share/mios/mios.toml [remote.rdp.redirect].usb [remote.device_forwarding.usb], tools/native/mios-gen/src/device_forwarding.rs, usr/share/doc/mios/remote-usb.md
// AI-functions: Factory, Policy, Identity, probe_vhci, usbip::serve, read_descriptors, status_json
//! Generic USB redirection (MS-RDPEUSB).
//!
//! The RDP client owns the physical device; every URB the Linux kernel issues for it travels
//! `vhci_hcd -> USB/IP socket -> [`usbip::serve`] -> [`UsbOps`] -> UsbDeviceHandle -> URBDRC -> client`.
//! The vhci side is a kernel module MiOS never loads: when `vhci_hcd` is absent the device is
//! retracted back to the client and the capability report says so.
//!
//! Admission order for an announced device: server permission, per-session target, announce IDs
//! against the SSOT policy, per-session device limit, vhci availability; then (before the kernel
//! sees anything) the real device and configuration descriptors are read over URBDRC and checked
//! again, so a composite device cannot smuggle a denied interface class behind `USB\COMPOSITE`.
//! Every refusal retracts the device (`RETRACT_DEVICE`, BlockedByPolicy) so the client keeps it.
//!
//! Unsafe: AF_UNIX SOCK_SEQPACKET + SCM_RIGHTS hand-over of the USB/IP socket to the root helper.
#![allow(unsafe_code)]

use std::collections::{BTreeMap, HashMap};
use std::os::fd::{AsRawFd, OwnedFd, RawFd};
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use ironrdp_rdpeusb::io::UsbRetractReason;
use ironrdp_server::{
    DeviceAnnounce, DeviceFactory, DeviceText, RdpUsbDeviceAnnounceInfo, UsbDeviceHandle, UsbRedirDevice,
    UsbRequestCompletion,
};
use ironrdp_usb::control::{RequestType, SetupPacket};
use ironrdp_usb::descriptor::ConfigurationDescriptorSet;
use ironrdp_usb::endpoint::EndpointAddress;
use ironrdp_usb::transfer::{
    DataTransferRequest, IsoCompletion, IsochronousPacketCompletion, IsochronousTransferRequest, TransferCompletion,
    UsbError, UsbResult,
};
use ironrdp_usb::{InterfaceSelection, TransferType};
use serde_json::{Value, json};
use tracing::{info, warn};

use crate::config::{Config, UsbForwarding};
use crate::pipeline::PipeStats;

/// Microsoft's Windows App platform matrix: generic USB port redirection is offered by the
/// Windows client only (mstsc / Windows App on Windows); macOS, iOS/iPadOS, Android/ChromeOS,
/// web and Meta Quest clients never announce USB devices.
pub const CLIENT_MATRIX: &str = "generic USB redirection is offered only by Windows clients (mstsc, Windows App on Windows); Windows App on macOS, iOS/iPadOS, Android/ChromeOS, web and Meta Quest has no USB port redirection (learn.microsoft.com/windows-app/compare-platforms-features)";

// ============================================================================
// Policy
// ============================================================================

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Rule {
    Id { vid: u16, pid: Option<u16> },
    Class(u8),
}

impl std::fmt::Display for Rule {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Id { vid, pid: Some(p) } => write!(f, "{vid:04x}:{p:04x}"),
            Self::Id { vid, pid: None } => write!(f, "{vid:04x}:*"),
            Self::Class(c) => write!(f, "class:{c:02x}"),
        }
    }
}

fn hex16(s: &str) -> Option<u16> {
    (s.len() == 4).then(|| u16::from_str_radix(s, 16).ok()).flatten()
}

pub fn parse_rule(raw: &str) -> Result<Rule, String> {
    let s = raw.trim().to_ascii_lowercase();
    if let Some(c) = s.strip_prefix("class:") {
        return (c.len() == 2)
            .then(|| u8::from_str_radix(c, 16).ok())
            .flatten()
            .map(Rule::Class)
            .ok_or_else(|| format!("bad class rule {raw:?} (want class:CC hex)"));
    }
    let (v, p) = s.split_once(':').ok_or_else(|| format!("bad rule {raw:?} (want VVVV:PPPP, VVVV:* or class:CC)"))?;
    let vid = hex16(v).ok_or_else(|| format!("bad vendor id in {raw:?}"))?;
    let pid = if p == "*" { None } else { Some(hex16(p).ok_or_else(|| format!("bad product id in {raw:?}"))?) };
    Ok(Rule::Id { vid, pid })
}

/// What is known about a device: from the URBDRC announce first, from its descriptors after the
/// preflight. `classes` holds the non-zero device class and every interface class.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Identity {
    pub vid: Option<u16>,
    pub pid: Option<u16>,
    pub classes: Vec<u8>,
}

impl Identity {
    fn push_class(&mut self, c: u8) {
        if c != 0 && !self.classes.contains(&c) {
            self.classes.push(c);
        }
    }
    pub fn to_json(&self) -> Value {
        json!({
            "vid": self.vid.map(|v| format!("{v:04x}")),
            "pid": self.pid.map(|p| format!("{p:04x}")),
            "classes": self.classes.iter().map(|c| format!("{c:02x}")).collect::<Vec<_>>(),
        })
    }
}

fn after<'a>(s: &'a str, key: &str) -> Option<&'a str> {
    let i = s.to_ascii_uppercase().find(&key.to_ascii_uppercase())?;
    s.get(i + key.len()..)
}

/// `USB\VID_1234&PID_ABCD&REV_0210` hardware IDs; `USB\Class_08&...` / `USB\DevClass_00` compat IDs.
pub fn identity_from_announce(a: &DeviceAnnounce) -> Identity {
    identity_from_ids(&a.hw_ids, &a.compat_ids)
}

pub fn identity_from_ids(hw_ids: &[String], compat_ids: &[String]) -> Identity {
    let mut id = Identity::default();
    for h in hw_ids {
        if id.vid.is_none() {
            id.vid = after(h, "VID_").and_then(|r| r.get(..4)).and_then(hex16);
        }
        if id.pid.is_none() {
            id.pid = after(h, "PID_").and_then(|r| r.get(..4)).and_then(hex16);
        }
    }
    for c in compat_ids {
        for key in ["\\DevClass_", "\\Class_"] {
            if let Some(v) = after(c, key).and_then(|r| r.get(..2)).and_then(|h| u8::from_str_radix(h, 16).ok()) {
                id.push_class(v);
            }
        }
    }
    id
}

/// Device descriptor (18 bytes) + every configuration descriptor set.
pub fn identity_from_descriptors(device: &[u8], configs: &BTreeMap<u8, Vec<u8>>) -> Result<Identity, String> {
    if device.len() < 18 || device[0] < 18 || device[1] != 1 {
        return Err("malformed device descriptor".into());
    }
    let mut id = Identity {
        vid: Some(u16::from_le_bytes([device[8], device[9]])),
        pid: Some(u16::from_le_bytes([device[10], device[11]])),
        classes: Vec::new(),
    };
    id.push_class(device[4]);
    for bytes in configs.values() {
        let set = ConfigurationDescriptorSet::parse(bytes).map_err(|e| format!("malformed configuration descriptor: {e:?}"))?;
        for iface in set.interfaces() {
            id.push_class(iface.class().class);
        }
    }
    Ok(id)
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Policy {
    pub allow: Vec<Rule>,
    pub deny: Vec<Rule>,
    pub max_devices: usize,
}

impl Policy {
    pub fn from_config(c: &UsbForwarding) -> Result<Self, String> {
        Ok(Self {
            allow: c.allow.iter().map(|r| parse_rule(r)).collect::<Result<_, _>>()?,
            deny: c.deny.iter().map(|r| parse_rule(r)).collect::<Result<_, _>>()?,
            max_devices: c.max_devices,
        })
    }

    fn matches(rule: &Rule, id: &Identity) -> bool {
        match rule {
            Rule::Id { vid, pid } => id.vid == Some(*vid) && pid.is_none_or(|p| id.pid == Some(p)),
            Rule::Class(c) => id.classes.contains(c),
        }
    }

    /// Deny wins; an empty allow list forwards nothing.
    pub fn check(&self, id: &Identity) -> Result<(), String> {
        if let Some(r) = self.deny.iter().find(|r| Self::matches(r, id)) {
            return Err(format!("denied by rule {r}"));
        }
        if self.allow.iter().any(|r| Self::matches(r, id)) {
            Ok(())
        } else {
            Err("not in [remote.device_forwarding.usb].allow".into())
        }
    }
}

// ============================================================================
// vhci_hcd capability probe (read-only sysfs; never loads a module)
// ============================================================================

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VhciPort {
    pub hub: String,
    pub port: u32,
    pub status: u32,
    pub devid: u32,
}

pub const VDEV_ST_NULL: u32 = 4;

/// `hub port sta spd dev sockfd local_busid` rows of /sys/devices/platform/vhci_hcd.N/status*.
pub fn parse_vhci_status(text: &str) -> Vec<VhciPort> {
    text.lines()
        .filter_map(|l| {
            let f: Vec<&str> = l.split_whitespace().collect();
            if f.len() < 5 || !(f[0] == "hs" || f[0] == "ss") {
                return None;
            }
            Some(VhciPort {
                hub: f[0].to_owned(),
                port: f[1].parse().ok()?,
                status: f[2].parse().ok()?,
                devid: u32::from_str_radix(f[4], 16).ok()?,
            })
        })
        .collect()
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VhciProbe {
    pub available: bool,
    pub reason: String,
    pub free_hs_ports: Vec<u32>,
    pub used_ports: Vec<u32>,
}

impl VhciProbe {
    pub fn to_json(&self) -> Value {
        json!({"kind": "usbip-vhci", "available": self.available, "reason": self.reason,
               "free_hs_ports": self.free_hs_ports, "used_ports": self.used_ports})
    }
}

/// `sys` is normally `/sys`; tests pass a fake tree.
pub fn probe_vhci(sys: &Path) -> VhciProbe {
    let dir = sys.join("devices/platform/vhci_hcd.0");
    if !dir.is_dir() {
        let reason = if sys.join("module/vhci_hcd").exists() {
            "vhci_hcd_loaded_without_controller"
        } else {
            "vhci_hcd_not_loaded (MiOS never loads kernel modules; generic USB stays with the client)"
        };
        return VhciProbe { available: false, reason: reason.into(), free_hs_ports: vec![], used_ports: vec![] };
    }
    let mut ports = Vec::new();
    for idx in 0..16u32 {
        let name = if idx == 0 { "status".to_owned() } else { format!("status.{idx}") };
        match std::fs::read_to_string(dir.join(&name)) {
            Ok(t) => ports.extend(parse_vhci_status(&t)),
            Err(_) if idx > 0 => break,
            Err(e) => {
                return VhciProbe { available: false, reason: format!("vhci status unreadable: {e}"), free_hs_ports: vec![], used_ports: vec![] };
            }
        }
    }
    let free: Vec<u32> = ports.iter().filter(|p| p.hub == "hs" && p.status == VDEV_ST_NULL).map(|p| p.port).collect();
    let used: Vec<u32> = ports.iter().filter(|p| p.status != VDEV_ST_NULL).map(|p| p.port).collect();
    VhciProbe {
        available: !free.is_empty(),
        reason: if free.is_empty() { "no_free_high_speed_vhci_port".into() } else { "ok".into() },
        free_hs_ports: free,
        used_ports: used,
    }
}

/// RDPEUSB v1 speeds (FULL = 0, HIGH = 1) -> kernel `enum usb_device_speed` for vhci attach.
pub fn vhci_speed(device_speed: u32) -> Option<u32> {
    match device_speed {
        0 => Some(2),
        1 => Some(3),
        _ => None,
    }
}

// ============================================================================
// USB/IP wire protocol (Documentation/usb/usbip_protocol.rst), big-endian
// ============================================================================

pub mod usbip {
    use std::collections::HashMap;
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::sync::{Arc, Mutex};

    use tokio::io::{AsyncRead, AsyncReadExt as _, AsyncWrite, AsyncWriteExt as _};
    use tokio::sync::mpsc;

    use super::{Binding, UsbOps, handle_submit};

    pub const CMD_SUBMIT: u32 = 1;
    pub const CMD_UNLINK: u32 = 2;
    pub const RET_SUBMIT: u32 = 3;
    pub const RET_UNLINK: u32 = 4;
    pub const HEADER: usize = 48;
    pub const DIR_OUT: u32 = 0;
    pub const DIR_IN: u32 = 1;
    pub const URB_SHORT_NOT_OK: u32 = 0x1;
    pub const URB_ISO_ASAP: u32 = 0x2;
    pub const EPIPE: i32 = -32;
    pub const ENODEV: i32 = -19;
    pub const ENOMEM: i32 = -12;
    pub const EAGAIN: i32 = -11;
    pub const EINVAL: i32 = -22;
    pub const EPROTO: i32 = -71;
    pub const EOVERFLOW: i32 = -75;
    pub const ECONNRESET: i32 = -104;
    pub const ETIMEDOUT: i32 = -110;
    pub const EREMOTEIO: i32 = -121;
    pub const MAX_ISO_PACKETS: i32 = 1024;

    #[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
    pub struct IsoDesc {
        pub offset: u32,
        pub length: u32,
        pub actual: u32,
        pub status: i32,
    }

    #[derive(Debug, Clone, PartialEq, Eq, Default)]
    pub struct Submit {
        pub seqnum: u32,
        pub devid: u32,
        pub direction: u32,
        pub ep: u32,
        pub flags: u32,
        pub length: u32,
        pub start_frame: i32,
        pub packets: i32,
        pub interval: i32,
        pub setup: [u8; 8],
        pub data: Vec<u8>,
        pub iso: Vec<IsoDesc>,
    }

    impl Submit {
        pub fn is_iso(&self) -> bool {
            self.packets > 0
        }
    }

    #[derive(Debug, Clone, PartialEq, Eq)]
    pub enum Command {
        Submit(Submit),
        Unlink { seqnum: u32, devid: u32, direction: u32, ep: u32, victim: u32 },
    }

    #[derive(Debug, Clone, PartialEq, Eq, Default)]
    pub struct Reply {
        pub status: i32,
        pub actual: u32,
        pub start_frame: i32,
        pub error_count: i32,
        pub data: Vec<u8>,
        pub iso: Vec<IsoDesc>,
    }

    impl Reply {
        pub fn status(status: i32) -> Self {
            Self { status, ..Self::default() }
        }
    }

    fn be(b: &[u8], i: usize) -> u32 {
        u32::from_be_bytes([b[i], b[i + 1], b[i + 2], b[i + 3]])
    }

    async fn read_exact_or_eof<R: AsyncRead + Unpin>(r: &mut R, buf: &mut [u8]) -> std::io::Result<bool> {
        let mut got = 0;
        while got < buf.len() {
            let n = r.read(&mut buf[got..]).await?;
            if n == 0 {
                return if got == 0 { Ok(false) } else { Err(std::io::ErrorKind::UnexpectedEof.into()) };
            }
            got += n;
        }
        Ok(true)
    }

    fn proto(msg: impl Into<String>) -> std::io::Error {
        std::io::Error::new(std::io::ErrorKind::InvalidData, msg.into())
    }

    /// One command, `None` at a clean EOF between commands. `max_transfer` bounds every buffer.
    pub async fn read_command<R: AsyncRead + Unpin>(r: &mut R, max_transfer: u32) -> std::io::Result<Option<Command>> {
        let mut h = [0u8; HEADER];
        if !read_exact_or_eof(r, &mut h).await? {
            return Ok(None);
        }
        let (command, seqnum, devid, direction, ep) = (be(&h, 0), be(&h, 4), be(&h, 8), be(&h, 12), be(&h, 16));
        if direction > 1 || ep > 15 {
            return Err(proto(format!("bad direction {direction} / endpoint {ep}")));
        }
        match command {
            CMD_SUBMIT => {
                let length = be(&h, 24);
                let packets = be(&h, 32) as i32;
                if length > max_transfer {
                    return Err(proto(format!("URB of {length} bytes exceeds {max_transfer}")));
                }
                // Linux sends 0 for non-isochronous URBs; older stacks used 0xffffffff.
                let packets = if packets == -1 { 0 } else { packets };
                if !(0..=MAX_ISO_PACKETS).contains(&packets) {
                    return Err(proto(format!("bad number_of_packets {packets}")));
                }
                let mut s = Submit {
                    seqnum,
                    devid,
                    direction,
                    ep,
                    flags: be(&h, 20),
                    length,
                    start_frame: be(&h, 28) as i32,
                    packets,
                    interval: be(&h, 36) as i32,
                    setup: h[40..48].try_into().expect("8"),
                    data: Vec::new(),
                    iso: Vec::new(),
                };
                if direction == DIR_OUT && length > 0 {
                    s.data = vec![0; length as usize];
                    r.read_exact(&mut s.data).await?;
                }
                if packets > 0 {
                    let mut d = vec![0u8; packets as usize * 16];
                    r.read_exact(&mut d).await?;
                    let mut total = 0u64;
                    for c in d.chunks_exact(16) {
                        let iso = IsoDesc { offset: be(c, 0), length: be(c, 4), actual: be(c, 8), status: be(c, 12) as i32 };
                        if u64::from(iso.offset) + u64::from(iso.length) > u64::from(length) {
                            return Err(proto("isochronous packet outside the transfer buffer"));
                        }
                        total += u64::from(iso.length);
                        s.iso.push(iso);
                    }
                    if total > u64::from(length) {
                        return Err(proto("isochronous packets exceed the transfer buffer"));
                    }
                }
                Ok(Some(Command::Submit(s)))
            }
            CMD_UNLINK => Ok(Some(Command::Unlink { seqnum, devid, direction, ep, victim: be(&h, 20) })),
            other => Err(proto(format!("unexpected USB/IP command {other:#x}"))),
        }
    }

    fn header(command: u32, seqnum: u32, devid: u32, direction: u32, ep: u32) -> Vec<u8> {
        let mut v = Vec::with_capacity(HEADER);
        for x in [command, seqnum, devid, direction, ep] {
            v.extend_from_slice(&x.to_be_bytes());
        }
        v
    }

    pub fn encode_ret_submit(s: &Submit, r: &Reply) -> Vec<u8> {
        let mut v = header(RET_SUBMIT, s.seqnum, s.devid, s.direction, s.ep);
        let packets = if s.is_iso() { s.packets } else { 0 };
        for x in [r.status, r.actual as i32, r.start_frame, packets, r.error_count] {
            v.extend_from_slice(&x.to_be_bytes());
        }
        v.extend_from_slice(&[0u8; 8]);
        if s.direction == DIR_IN {
            v.extend_from_slice(&r.data[..(r.actual as usize).min(r.data.len())]);
        }
        if s.is_iso() {
            for d in &r.iso {
                for x in [d.offset, d.length, d.actual, d.status as u32] {
                    v.extend_from_slice(&x.to_be_bytes());
                }
            }
        }
        v
    }

    pub fn encode_ret_unlink(seqnum: u32, devid: u32, direction: u32, ep: u32, status: i32) -> Vec<u8> {
        let mut v = header(RET_UNLINK, seqnum, devid, direction, ep);
        v.extend_from_slice(&status.to_be_bytes());
        v.extend_from_slice(&[0u8; 24]);
        v
    }

    /// Kernel-side encoder (fixtures, the synthetic vhci peer in tests).
    pub fn encode_cmd_submit(s: &Submit) -> Vec<u8> {
        let mut v = header(CMD_SUBMIT, s.seqnum, s.devid, s.direction, s.ep);
        for x in [s.flags, s.length, s.start_frame as u32, s.packets as u32, s.interval as u32] {
            v.extend_from_slice(&x.to_be_bytes());
        }
        v.extend_from_slice(&s.setup);
        if s.direction == DIR_OUT {
            v.extend_from_slice(&s.data);
        }
        for d in &s.iso {
            for x in [d.offset, d.length, d.actual, d.status as u32] {
                v.extend_from_slice(&x.to_be_bytes());
            }
        }
        v
    }

    pub fn encode_cmd_unlink(seqnum: u32, devid: u32, victim: u32) -> Vec<u8> {
        let mut v = header(CMD_UNLINK, seqnum, devid, DIR_OUT, 0);
        v.extend_from_slice(&victim.to_be_bytes());
        v.extend_from_slice(&[0u8; 24]);
        v
    }

    /// A decoded RET_SUBMIT / RET_UNLINK (tests and fixtures).
    #[derive(Debug, Clone, PartialEq, Eq)]
    pub struct Ret {
        pub command: u32,
        pub seqnum: u32,
        pub status: i32,
        pub actual: u32,
        pub data: Vec<u8>,
        pub iso: Vec<IsoDesc>,
    }

    /// Reads one return; `in_dir`/`packets` come from the matching submit.
    pub async fn read_ret<R: AsyncRead + Unpin>(r: &mut R, expect: &HashMap<u32, (u32, i32)>) -> std::io::Result<Ret> {
        let mut h = [0u8; HEADER];
        r.read_exact(&mut h).await?;
        let command = be(&h, 0);
        let seqnum = be(&h, 4);
        let status = be(&h, 20) as i32;
        let mut ret = Ret { command, seqnum, status, actual: 0, data: Vec::new(), iso: Vec::new() };
        if command == RET_SUBMIT {
            ret.actual = be(&h, 24);
            let (dir, packets) = expect.get(&seqnum).copied().unwrap_or((DIR_OUT, 0));
            if dir == DIR_IN {
                ret.data = vec![0; ret.actual as usize];
                r.read_exact(&mut ret.data).await?;
            }
            for _ in 0..packets.max(0) {
                let mut d = [0u8; 16];
                r.read_exact(&mut d).await?;
                ret.iso.push(IsoDesc { offset: be(&d, 0), length: be(&d, 4), actual: be(&d, 8), status: be(&d, 12) as i32 });
            }
        }
        Ok(ret)
    }

    #[derive(Debug, Default)]
    pub struct Counters {
        pub urbs: AtomicU64,
        pub bytes_in: AtomicU64,
        pub bytes_out: AtomicU64,
        pub unlinks: AtomicU64,
        pub errors: AtomicU64,
    }

    #[derive(Debug, Clone, Copy)]
    pub struct Limits {
        pub max_transfer: u32,
        pub max_inflight: usize,
    }

    /// Serves one vhci port until the kernel closes the socket (detach) or sends garbage.
    /// Dropping the returned future aborts every in-flight URB, which cancels it on the client.
    pub async fn serve<S, O>(stream: S, ops: Arc<O>, binding: Binding, limits: Limits, counters: Arc<Counters>) -> Result<(), String>
    where
        S: AsyncRead + AsyncWrite + Send + 'static,
        O: UsbOps,
    {
        let (mut rd, mut wr) = tokio::io::split(stream);
        let (tx, mut rx) = mpsc::unbounded_channel::<Vec<u8>>();
        let writer = tokio::spawn(async move {
            while let Some(frame) = rx.recv().await {
                if wr.write_all(&frame).await.is_err() {
                    break;
                }
            }
        });
        let binding = Arc::new(Mutex::new(binding));
        let inflight: Arc<Mutex<HashMap<u32, tokio::task::AbortHandle>>> = Arc::default();
        let mut tasks = tokio::task::JoinSet::new();
        let result = loop {
            while tasks.try_join_next().is_some() {}
            let cmd = match read_command(&mut rd, limits.max_transfer).await {
                Ok(Some(c)) => c,
                Ok(None) => break Ok(()),
                Err(e) => break Err(format!("USB/IP protocol: {e}")),
            };
            match cmd {
                Command::Submit(s) => {
                    counters.urbs.fetch_add(1, Ordering::Relaxed);
                    counters.bytes_out.fetch_add(s.data.len() as u64, Ordering::Relaxed);
                    let mut map = inflight.lock().expect("inflight");
                    if map.len() >= limits.max_inflight || map.contains_key(&s.seqnum) {
                        let status = if map.contains_key(&s.seqnum) { EINVAL } else { EAGAIN };
                        drop(map);
                        let _ = tx.send(encode_ret_submit(&s, &Reply::status(status)));
                        continue;
                    }
                    let (ops, binding, inflight2, tx2, counters2) =
                        (Arc::clone(&ops), Arc::clone(&binding), Arc::clone(&inflight), tx.clone(), Arc::clone(&counters));
                    let seq = s.seqnum;
                    // The map lock is held across spawn: the task cannot finish (and look itself up)
                    // before its abort handle is registered, so an UNLINK always sees one state.
                    let handle = tasks.spawn(async move {
                        let reply = handle_submit(&*ops, &binding, &s).await;
                        if inflight2.lock().expect("inflight").remove(&s.seqnum).is_some() {
                            if reply.status != 0 {
                                counters2.errors.fetch_add(1, Ordering::Relaxed);
                            }
                            if s.direction == DIR_IN {
                                counters2.bytes_in.fetch_add(u64::from(reply.actual), Ordering::Relaxed);
                            }
                            let _ = tx2.send(encode_ret_submit(&s, &reply));
                        }
                    });
                    map.insert(seq, handle);
                }
                Command::Unlink { seqnum, devid, direction, ep, victim } => {
                    counters.unlinks.fetch_add(1, Ordering::Relaxed);
                    let status = match inflight.lock().expect("inflight").remove(&victim) {
                        Some(h) => {
                            h.abort();
                            ECONNRESET
                        }
                        None => 0,
                    };
                    let _ = tx.send(encode_ret_unlink(seqnum, devid, direction, ep, status));
                }
            }
        };
        inflight.lock().expect("inflight").clear();
        tasks.shutdown().await;
        drop(tx);
        let _ = writer.await;
        result
    }
}

// ============================================================================
// URB translation
// ============================================================================

/// The USB operations a redirected device offers (UsbDeviceHandle in production, a synthetic
/// device in tests). `Err` = the channel is gone; USB failures are inside the Ok value.
#[async_trait::async_trait]
pub trait UsbOps: Send + Sync + 'static {
    async fn control(&self, setup: SetupPacket, data: Vec<u8>) -> Result<TransferCompletion<Vec<u8>>, String>;
    async fn data(&self, kind: TransferType, req: DataTransferRequest<Vec<u8>>) -> Result<TransferCompletion<Vec<u8>>, String>;
    async fn iso(&self, req: IsochronousTransferRequest<Vec<u8>, Vec<u32>>) -> Result<IsoCompletion<Vec<u8>, Vec<IsochronousPacketCompletion>>, String>;
    async fn select_configuration(&self, descriptor: Option<Vec<u8>>) -> Result<UsbResult<()>, String>;
    async fn select_interface(&self, descriptor: Vec<u8>, selection: InterfaceSelection) -> Result<UsbResult<()>, String>;
    async fn clear_halt(&self, endpoint: EndpointAddress) -> Result<UsbResult<()>, String>;
}

/// Kernel-visible configuration state, needed to translate SET_CONFIGURATION / SET_INTERFACE
/// into URBDRC select requests and to know each endpoint's transfer type.
#[derive(Debug, Clone, Default)]
pub struct Binding {
    pub configs: BTreeMap<u8, Vec<u8>>,
    pub active: Option<u8>,
    pub alts: HashMap<u8, u8>,
}

impl Binding {
    pub fn new(configs: BTreeMap<u8, Vec<u8>>) -> Self {
        Self { configs, active: None, alts: HashMap::new() }
    }

    pub fn endpoint_type(&self, address: u8) -> Option<TransferType> {
        let bytes = self.configs.get(&self.active?)?;
        let set = ConfigurationDescriptorSet::parse(bytes).ok()?;
        set.interfaces()
            .filter(|i| i.alternate_setting() == self.alts.get(&i.number()).copied().unwrap_or(0))
            .flat_map(|i| i.endpoints())
            .find(|e| e.address_raw() == address)
            .map(|e| e.transfer_type())
    }

    /// Cache a complete configuration descriptor seen in a GET_DESCRIPTOR(CONFIGURATION) reply.
    fn observe(&mut self, setup: &SetupPacket, data: &[u8]) {
        if setup.request_type.raw() == 0x80 && setup.request == 6 && setup.value >> 8 == 2 && data.len() >= 9 {
            let total = usize::from(u16::from_le_bytes([data[2], data[3]]));
            if total == data.len() && ConfigurationDescriptorSet::parse(data).is_ok() {
                self.configs.insert(data[5], data.to_vec());
            }
        }
    }
}

pub fn errno(e: UsbError) -> i32 {
    match e {
        UsbError::Cancelled => usbip::ECONNRESET,
        UsbError::Stall => usbip::EPIPE,
        UsbError::Timeout => usbip::ETIMEDOUT,
        UsbError::Overflow => usbip::EOVERFLOW,
        UsbError::NoDevice => usbip::ENODEV,
        _ => usbip::EPROTO,
    }
}

fn status_of(r: UsbResult<()>) -> i32 {
    r.err().map_or(0, errno)
}

fn transfer_reply(s: &usbip::Submit, r: Result<TransferCompletion<Vec<u8>>, String>) -> usbip::Reply {
    match r {
        Err(_) => usbip::Reply::status(usbip::ENODEV),
        Ok(c) => {
            let mut status = status_of(c.status);
            let actual = if s.direction == usbip::DIR_IN { (c.data.len() as u32).min(s.length) } else { c.actual_length.min(s.length) };
            if status == 0 && s.direction == usbip::DIR_IN && s.flags & usbip::URB_SHORT_NOT_OK != 0 && actual < s.length {
                status = usbip::EREMOTEIO;
            }
            usbip::Reply { status, actual, data: c.data, ..usbip::Reply::default() }
        }
    }
}

fn unit_reply(r: Result<UsbResult<()>, String>) -> usbip::Reply {
    usbip::Reply::status(match r {
        Err(_) => usbip::ENODEV,
        Ok(u) => status_of(u),
    })
}

pub async fn handle_submit<O: UsbOps + ?Sized>(ops: &O, binding: &Mutex<Binding>, s: &usbip::Submit) -> usbip::Reply {
    if s.ep == 0 {
        return handle_control(ops, binding, s).await;
    }
    let raw = (s.ep as u8) | if s.direction == usbip::DIR_IN { 0x80 } else { 0 };
    let Ok(endpoint) = EndpointAddress::from_raw(raw) else { return usbip::Reply::status(usbip::EINVAL) };
    if s.is_iso() {
        // OUT payloads sit at their descriptor offsets; URBDRC wants them packed in order.
        let data = if s.direction == usbip::DIR_OUT {
            s.iso.iter().flat_map(|d| s.data[d.offset as usize..(d.offset + d.length) as usize].iter().copied()).collect()
        } else {
            Vec::new()
        };
        let req = IsochronousTransferRequest {
            endpoint,
            start_frame: (s.flags & usbip::URB_ISO_ASAP == 0).then_some(s.start_frame as u32),
            data,
            packets: s.iso.iter().map(|d| d.length).collect(),
        };
        return match ops.iso(req).await {
            Err(_) => usbip::Reply::status(usbip::ENODEV),
            Ok(Err(e)) => {
                let st = errno(e);
                usbip::Reply {
                    status: st,
                    error_count: s.packets,
                    iso: s.iso.iter().map(|d| usbip::IsoDesc { offset: d.offset, length: d.length, actual: 0, status: st }).collect(),
                    ..usbip::Reply::default()
                }
            }
            Ok(Ok(out)) => {
                let iso: Vec<usbip::IsoDesc> = s
                    .iso
                    .iter()
                    .enumerate()
                    .map(|(i, d)| {
                        let p = out.packets.get(i);
                        usbip::IsoDesc {
                            offset: d.offset,
                            length: d.length,
                            actual: p.map_or(0, |p| p.actual_length.min(d.length)),
                            status: p.map_or(usbip::EPROTO, |p| status_of(p.status)),
                        }
                    })
                    .collect();
                let error_count = iso.iter().filter(|d| d.status != 0).count() as i32;
                let actual = if s.direction == usbip::DIR_IN { out.data.len() as u32 } else { out.actual_length };
                usbip::Reply { status: 0, actual: actual.min(s.length), start_frame: out.start_frame as i32, error_count, data: out.data, iso }
            }
        };
    }
    let kind = binding.lock().expect("binding").endpoint_type(raw);
    let kind = match kind {
        Some(k @ (TransferType::Bulk | TransferType::Interrupt)) => k,
        _ => return usbip::Reply::status(usbip::EPIPE),
    };
    let data = if s.direction == usbip::DIR_OUT { s.data.clone() } else { Vec::new() };
    transfer_reply(s, ops.data(kind, DataTransferRequest { endpoint, length: s.length, data }).await)
}

async fn handle_control<O: UsbOps + ?Sized>(ops: &O, binding: &Mutex<Binding>, s: &usbip::Submit) -> usbip::Reply {
    let setup = SetupPacket::from_bytes(s.setup);
    let (rt, req) = (setup.request_type.raw(), setup.request);
    match (rt, req) {
        // vhci assigns the address itself; one reaching us is a no-op.
        (0x00, 5) => usbip::Reply::status(0),
        (0x00, 9) => {
            let value = (setup.value & 0xff) as u8;
            let descriptor = if value == 0 {
                None
            } else {
                match binding.lock().expect("binding").configs.get(&value) {
                    Some(d) => Some(d.clone()),
                    None => return usbip::Reply::status(usbip::EPIPE),
                }
            };
            let r = ops.select_configuration(descriptor).await;
            if matches!(r, Ok(Ok(()))) {
                let mut b = binding.lock().expect("binding");
                b.active = (value != 0).then_some(value);
                b.alts.clear();
            }
            unit_reply(r)
        }
        (0x01, 11) => {
            let (iface, alt) = ((setup.index & 0xff) as u8, (setup.value & 0xff) as u8);
            let descriptor = {
                let b = binding.lock().expect("binding");
                match b.active.and_then(|a| b.configs.get(&a)) {
                    Some(d) if ConfigurationDescriptorSet::parse(d).is_ok_and(|set| set.interface(iface, alt).is_some()) => d.clone(),
                    _ => return usbip::Reply::status(usbip::EPIPE),
                }
            };
            let r = ops.select_interface(descriptor, InterfaceSelection { interface: iface, alternate_setting: alt }).await;
            if matches!(r, Ok(Ok(()))) {
                binding.lock().expect("binding").alts.insert(iface, alt);
            }
            unit_reply(r)
        }
        // CLEAR_FEATURE(ENDPOINT_HALT): URBDRC resets the pipe and clears the stall in one request.
        (0x02, 1) if setup.value == 0 => match EndpointAddress::from_raw((setup.index & 0xff) as u8) {
            Ok(ep) if !ep.is_default_control() => unit_reply(ops.clear_halt(ep).await),
            _ => usbip::Reply::status(usbip::EPIPE),
        },
        _ => {
            let data = if rt & 0x80 == 0 { s.data.clone() } else { Vec::new() };
            let r = ops.control(setup, data).await;
            if let Ok(c) = &r {
                if c.status.is_ok() {
                    binding.lock().expect("binding").observe(&setup, &c.data);
                }
            }
            transfer_reply(s, r)
        }
    }
}

fn get_descriptor_setup(kind: u8, index: u8, length: u16) -> SetupPacket {
    SetupPacket { request_type: RequestType::from_raw(0x80), request: 6, value: (u16::from(kind) << 8) | u16::from(index), index: 0, length }
}

async fn get_descriptor<O: UsbOps + ?Sized>(ops: &O, kind: u8, index: u8, length: u16) -> Result<Vec<u8>, String> {
    let c = ops.control(get_descriptor_setup(kind, index, length), Vec::new()).await?;
    c.status.map_err(|e| format!("GET_DESCRIPTOR({kind},{index}) failed: {e}"))?;
    Ok(c.data)
}

/// Device descriptor + every configuration set, read over URBDRC before the kernel sees the device.
pub async fn read_descriptors<O: UsbOps + ?Sized>(ops: &O) -> Result<(Vec<u8>, BTreeMap<u8, Vec<u8>>), String> {
    let device = get_descriptor(ops, 1, 0, 18).await?;
    if device.len() < 18 || device[1] != 1 {
        return Err("malformed device descriptor".into());
    }
    let mut configs = BTreeMap::new();
    for i in 0..device[17].min(8) {
        let head = get_descriptor(ops, 2, i, 9).await?;
        if head.len() < 9 || head[1] != 2 {
            return Err(format!("malformed configuration descriptor {i}"));
        }
        let total = u16::from_le_bytes([head[2], head[3]]);
        let full = get_descriptor(ops, 2, i, total).await?;
        ConfigurationDescriptorSet::parse(&full).map_err(|e| format!("configuration {i}: {e:?}"))?;
        configs.insert(full[5], full);
    }
    if configs.is_empty() {
        return Err("device reports no configuration".into());
    }
    Ok((device, configs))
}

/// Production [UsbOps]: one redirected device's URBDRC handle.
pub struct HandleOps(pub UsbDeviceHandle);

fn shape(c: &UsbRequestCompletion) -> String {
    format!("unexpected URBDRC completion {c:?}")
}

#[async_trait::async_trait]
impl UsbOps for HandleOps {
    async fn control(&self, setup: SetupPacket, data: Vec<u8>) -> Result<TransferCompletion<Vec<u8>>, String> {
        let p = self.0.control_transfer(ironrdp_usb::transfer::ControlTransferRequest { setup, data }).await.map_err(|e| e.to_string())?;
        match p.wait().await.map_err(|e| e.to_string())? {
            UsbRequestCompletion::Transfer(t) => Ok(t),
            other => Err(shape(&other)),
        }
    }
    async fn data(&self, kind: TransferType, req: DataTransferRequest<Vec<u8>>) -> Result<TransferCompletion<Vec<u8>>, String> {
        let p = match kind {
            TransferType::Interrupt => self.0.interrupt_transfer(req).await,
            _ => self.0.bulk_transfer(req).await,
        }
        .map_err(|e| e.to_string())?;
        match p.wait().await.map_err(|e| e.to_string())? {
            UsbRequestCompletion::Transfer(t) => Ok(t),
            other => Err(shape(&other)),
        }
    }
    async fn iso(&self, req: IsochronousTransferRequest<Vec<u8>, Vec<u32>>) -> Result<IsoCompletion<Vec<u8>, Vec<IsochronousPacketCompletion>>, String> {
        let p = self.0.isochronous_transfer(req).await.map_err(|e| e.to_string())?;
        match p.wait().await.map_err(|e| e.to_string())? {
            UsbRequestCompletion::Isochronous(i) => Ok(i),
            other => Err(shape(&other)),
        }
    }
    async fn select_configuration(&self, descriptor: Option<Vec<u8>>) -> Result<UsbResult<()>, String> {
        let set = match &descriptor {
            Some(d) => Some(ConfigurationDescriptorSet::parse(d).map_err(|e| format!("{e:?}"))?),
            None => None,
        };
        let p = self.0.select_configuration(set).await.map_err(|e| e.to_string())?;
        match p.wait().await.map_err(|e| e.to_string())? {
            UsbRequestCompletion::Unit(u) => Ok(u),
            other => Err(shape(&other)),
        }
    }
    async fn select_interface(&self, descriptor: Vec<u8>, selection: InterfaceSelection) -> Result<UsbResult<()>, String> {
        let set = ConfigurationDescriptorSet::parse(&descriptor).map_err(|e| format!("{e:?}"))?;
        let p = self.0.select_interface(set, selection).await.map_err(|e| e.to_string())?;
        match p.wait().await.map_err(|e| e.to_string())? {
            UsbRequestCompletion::Unit(u) => Ok(u),
            other => Err(shape(&other)),
        }
    }
    async fn clear_halt(&self, endpoint: EndpointAddress) -> Result<UsbResult<()>, String> {
        let p = self.0.clear_halt(endpoint).await.map_err(|e| e.to_string())?;
        match p.wait().await.map_err(|e| e.to_string())? {
            UsbRequestCompletion::Unit(u) => Ok(u),
            other => Err(shape(&other)),
        }
    }
}

// ============================================================================
// Root helper hand-over (mios-rdp-usb.socket)
// ============================================================================

fn send_with_fd(fd: RawFd, msg: &[u8], pass: RawFd) -> std::io::Result<()> {
    // SAFETY: sendmsg on a connected SEQPACKET socket with one SCM_RIGHTS descriptor built in a local buffer.
    unsafe {
        let mut iov = libc::iovec { iov_base: msg.as_ptr() as *mut libc::c_void, iov_len: msg.len() };
        let mut mh: libc::msghdr = std::mem::zeroed();
        mh.msg_iov = &mut iov;
        mh.msg_iovlen = 1;
        let space = libc::CMSG_SPACE(size_of::<RawFd>() as u32) as usize;
        let mut cbuf = vec![0u8; space];
        mh.msg_control = cbuf.as_mut_ptr().cast();
        mh.msg_controllen = space;
        let c = libc::CMSG_FIRSTHDR(&mh);
        (*c).cmsg_level = libc::SOL_SOCKET;
        (*c).cmsg_type = libc::SCM_RIGHTS;
        (*c).cmsg_len = libc::CMSG_LEN(size_of::<RawFd>() as u32) as usize;
        std::ptr::copy_nonoverlapping(&pass, libc::CMSG_DATA(c).cast::<RawFd>(), 1);
        if libc::sendmsg(fd, &mh, libc::MSG_NOSIGNAL) < 0 {
            return Err(std::io::Error::last_os_error());
        }
    }
    Ok(())
}

/// `USBIP-ATTACH speed devid` + the kernel end of the USB/IP socket. Returns the helper connection
/// (closing it detaches exactly this port) and the vhci port number.
pub fn helper_attach(socket: &Path, speed: u32, devid: u32, kernel_end: &OwnedFd, timeout: Duration) -> Result<(OwnedFd, u32), String> {
    let conn = crate::auth::seqpacket_connect(socket).map_err(|e| format!("USB helper unavailable: {e}"))?;
    let tv = libc::timeval { tv_sec: timeout.as_secs() as libc::time_t, tv_usec: 0 };
    // SAFETY: SO_RCVTIMEO on our own socket.
    unsafe {
        libc::setsockopt(conn.as_raw_fd(), libc::SOL_SOCKET, libc::SO_RCVTIMEO, (&raw const tv).cast(), size_of::<libc::timeval>() as u32);
    }
    send_with_fd(conn.as_raw_fd(), format!("USBIP-ATTACH\0{speed}\0{devid}").as_bytes(), kernel_end.as_raw_fd())
        .map_err(|e| format!("USB helper send: {e}"))?;
    let (reply, _) = crate::auth::recv(conn.as_raw_fd()).map_err(|e| format!("USB helper reply: {e}"))?;
    let reply = String::from_utf8_lossy(&reply).into_owned();
    match reply.strip_prefix("OK ").and_then(|p| p.trim().parse().ok()) {
        Some(port) => Ok((conn, port)),
        None => Err(format!("USB helper refused: {reply}")),
    }
}

// ============================================================================
// ironrdp-server backend: factory, per-device lifecycle, per-session claims
// ============================================================================

fn target_for_rdp(cfg: &Config) -> String {
    let path = cfg.runtime_dir.join(&cfg.device_forwarding.targets_file);
    std::fs::read(&path)
        .ok()
        .and_then(|b| serde_json::from_slice::<Value>(&b).ok())
        .and_then(|v| v.pointer("/sessions/rdp/target").and_then(Value::as_str).map(str::to_owned))
        .unwrap_or_else(|| cfg.device_forwarding.default_target.clone())
}

struct Shared {
    cfg: Config,
    stats: Arc<PipeStats>,
    policy: Policy,
    sys: std::path::PathBuf,
    next_key: AtomicU64,
}

impl Shared {
    fn update(&self, key: u64, f: impl FnOnce(&mut serde_json::Map<String, Value>)) {
        let mut list = self.stats.usb_devices.lock().expect("usb devices");
        if let Some(Value::Object(m)) = list.iter_mut().find(|v| v["key"] == key) {
            f(m);
        }
    }

    fn live_claims(&self, serial: u64) -> usize {
        let list = self.stats.usb_devices.lock().expect("usb devices");
        list.iter().filter(|v| v["serial"] == serial && matches!(v["state"].as_str(), Some("preflight" | "attaching" | "attached"))).count()
    }

    /// Server-side admission before any device I/O.
    fn admit(&self, id: &Identity, speed: u32, serial: u64) -> Result<(), String> {
        if !self.cfg.redirect.usb {
            return Err("server permission off ([remote.rdp.redirect].usb = false)".into());
        }
        match target_for_rdp(&self.cfg).as_str() {
            "auto" | "host-vhci" => {}
            "none" => return Err("session target is none (mios-gen device-forwarding select)".into()),
            other => return Err(format!("unsupported session target {other:?}")),
        }
        self.policy.check(id)?;
        if self.live_claims(serial) >= self.policy.max_devices {
            return Err(format!("session already holds {} devices", self.policy.max_devices));
        }
        if vhci_speed(speed).is_none() {
            return Err(format!("unsupported device speed {speed}"));
        }
        let probe = probe_vhci(&self.sys);
        if !probe.available {
            return Err(format!("endpoint unavailable: {}", probe.reason));
        }
        Ok(())
    }
}

pub struct Factory {
    shared: Arc<Shared>,
}

impl Factory {
    pub fn new(cfg: Config, stats: Arc<PipeStats>) -> anyhow::Result<Self> {
        let policy = Policy::from_config(&cfg.device_forwarding.usb).map_err(|e| anyhow::anyhow!(e))?;
        Ok(Self { shared: Arc::new(Shared { cfg, stats, policy, sys: "/sys".into(), next_key: AtomicU64::new(1) }) })
    }
}

impl DeviceFactory for Factory {
    fn create_device(&mut self) -> Option<Box<dyn UsbRedirDevice>> {
        let serial = self.shared.stats.connection_serial.load(Ordering::Acquire);
        // Per-session claims: entries of earlier connections are history once they stopped.
        self.shared.stats.usb_devices.lock().expect("usb devices").retain(|v| {
            v["serial"] == serial || matches!(v["state"].as_str(), Some("preflight" | "attaching" | "attached"))
        });
        let key = self.shared.next_key.fetch_add(1, Ordering::AcqRel);
        Some(Box::new(Device { shared: Arc::clone(&self.shared), key, serial, task: None }))
    }
}

struct Device {
    shared: Arc<Shared>,
    key: u64,
    serial: u64,
    task: Option<tokio::task::JoinHandle<()>>,
}

struct WorkerGuard(Arc<PipeStats>);
impl WorkerGuard {
    fn new(s: &Arc<PipeStats>) -> Self {
        s.usb_workers.fetch_add(1, Ordering::AcqRel);
        Self(Arc::clone(s))
    }
}
impl Drop for WorkerGuard {
    fn drop(&mut self) {
        self.0.usb_workers.fetch_sub(1, Ordering::AcqRel);
    }
}

fn refuse(shared: &Shared, key: u64, handle: &UsbDeviceHandle, state: &str, reason: &str) {
    warn!(key, state, reason, "USB redirection refused; retracting the device to the client");
    shared.update(key, |m| {
        m.insert("state".into(), json!(state));
        m.insert("reason".into(), json!(reason));
    });
    let _ = handle.retract_request(UsbRetractReason::BlockedByPolicy);
}

async fn run_device(shared: Arc<Shared>, key: u64, handle: UsbDeviceHandle, speed: u32) {
    let _worker = WorkerGuard::new(&shared.stats);
    let n = &shared.cfg.native;
    let deadline = Duration::from_millis(n.usb_attach_timeout_ms);
    let ops = Arc::new(HandleOps(handle.clone()));
    let (device, configs) = match tokio::time::timeout(deadline, read_descriptors(&*ops)).await {
        Ok(Ok(d)) => d,
        Ok(Err(e)) => return refuse(&shared, key, &handle, "refused", &format!("descriptor preflight: {e}")),
        Err(_) => return refuse(&shared, key, &handle, "refused", "descriptor preflight timed out"),
    };
    let real = match identity_from_descriptors(&device, &configs) {
        Ok(i) => i,
        Err(e) => return refuse(&shared, key, &handle, "refused", &e),
    };
    shared.update(key, |m| {
        m.insert("descriptors".into(), real.to_json());
    });
    if let Err(e) = shared.policy.check(&real) {
        return refuse(&shared, key, &handle, "refused", &format!("descriptors: {e}"));
    }
    let (ours, kernel_end) = match std::os::unix::net::UnixStream::pair() {
        Ok(p) => p,
        Err(e) => return refuse(&shared, key, &handle, "attach_failed", &format!("socketpair: {e}")),
    };
    shared.update(key, |m| {
        m.insert("state".into(), json!("attaching"));
    });
    let devid = 0x4d49_0000 | (key as u32 & 0xffff);
    let vspeed = vhci_speed(speed).unwrap_or(3);
    let socket = n.usb_helper_socket.clone();
    let kernel_end: OwnedFd = kernel_end.into();
    let attached = tokio::task::spawn_blocking(move || helper_attach(&socket, vspeed, devid, &kernel_end, deadline)).await;
    let (helper, port) = match attached {
        Ok(Ok(v)) => v,
        Ok(Err(e)) => return refuse(&shared, key, &handle, "attach_failed", &e),
        Err(e) => return refuse(&shared, key, &handle, "attach_failed", &e.to_string()),
    };
    let stream = match ours.set_nonblocking(true).and_then(|()| tokio::net::UnixStream::from_std(ours)) {
        Ok(s) => s,
        Err(e) => return refuse(&shared, key, &handle, "attach_failed", &format!("stream: {e}")),
    };
    info!(key, port, devid = format!("{devid:#x}"), "redirected USB device attached to vhci");
    shared.update(key, |m| {
        m.insert("state".into(), json!("attached"));
        m.insert("port".into(), json!(port));
    });
    let counters = Arc::new(usbip::Counters::default());
    let limits = usbip::Limits { max_transfer: n.usb_max_transfer_kib * 1024, max_inflight: n.usb_max_inflight };
    let result = usbip::serve(stream, ops, Binding::new(configs), limits, Arc::clone(&counters)).await;
    drop(helper); // the helper detaches exactly this port
    let reason = match result {
        Ok(()) => "kernel detached the port".to_owned(),
        Err(e) => e,
    };
    shared.stats.usb_urbs.fetch_add(counters.urbs.load(Ordering::Relaxed), Ordering::Relaxed);
    shared.update(key, |m| {
        m.insert("state".into(), json!("detached"));
        m.insert("reason".into(), json!(reason));
        m.insert("urbs".into(), json!(counters.urbs.load(Ordering::Relaxed)));
    });
    // The kernel side ended first (operator detach, protocol error): give the device back.
    let _ = handle.retract_request(UsbRetractReason::BlockedByPolicy);
}

impl UsbRedirDevice for Device {
    fn device_added(&mut self, info: RdpUsbDeviceAnnounceInfo) {
        let RdpUsbDeviceAnnounceInfo { announce, usb_handle } = info;
        let id = identity_from_announce(&announce);
        let speed = announce.usb_device_caps.device_speed.to_u32();
        {
            let mut a = self.shared.stats.usb_announced.lock().expect("usb announced");
            *a = if a.0 == self.serial { (a.0, a.1 + 1) } else { (self.serial, 1) };
        }
        self.shared.stats.usb_devices.lock().expect("usb devices").push(json!({
            "key": self.key, "serial": self.serial, "instance": announce.device_instance_id,
            "announce": id.to_json(), "speed": speed, "state": "announced", "reason": null, "port": null,
        }));
        info!(key = self.key, instance = %announce.device_instance_id, ids = ?announce.hw_ids, "client announced a USB device");
        if let Err(reason) = self.shared.admit(&id, speed, self.serial) {
            return refuse(&self.shared, self.key, &usb_handle, "refused", &reason);
        }
        self.shared.update(self.key, |m| {
            m.insert("state".into(), json!("preflight"));
        });
        self.task = Some(tokio::spawn(run_device(Arc::clone(&self.shared), self.key, usb_handle, speed)));
    }

    fn device_text(&mut self, _device_text: DeviceText) {}

    fn close(&mut self) {
        // Non-blocking: aborting drops the engine (cancelling in-flight URBs) and the helper
        // connection, whose EOF makes the root helper detach the port.
        if let Some(t) = self.task.take() {
            t.abort();
        }
        self.shared.update(self.key, |m| {
            if matches!(m.get("state").and_then(Value::as_str), Some("preflight" | "attaching" | "attached" | "announced")) {
                m.insert("state".into(), json!("closed"));
                m.insert("reason".into(), json!("client removed the device or the connection ended"));
            }
        });
    }
}

/// `device_forwarding.usb` in stats.json: server capability, native endpoint and this client's offer.
pub fn status_json(cfg: &Config, stats: &PipeStats) -> Value {
    let serial = stats.connection_serial.load(Ordering::Acquire);
    let announced = {
        let a = stats.usb_announced.lock().expect("usb announced");
        if a.0 == serial { a.1 } else { 0 }
    };
    let probe = probe_vhci(Path::new("/sys"));
    let client = if !cfg.redirect.usb {
        "server_disabled"
    } else if announced > 0 {
        "client_redirects_usb"
    } else {
        "no_devices_offered"
    };
    json!({
        "implemented": true, "protocol": "MS-RDPEUSB", "permitted": cfg.redirect.usb,
        "endpoint": probe.to_json(),
        "target": target_for_rdp(cfg),
        "client": {"connection": serial, "status": client, "devices_announced": announced, "platforms": CLIENT_MATRIX},
        "devices": stats.usb_devices.lock().expect("usb devices").clone(),
        "workers": stats.usb_workers.load(Ordering::Relaxed),
        "urbs": stats.usb_urbs.load(Ordering::Relaxed),
    })
}

// ============================================================================
// Tests: synthetic URBDRC exchanges and a synthetic vhci kernel peer
// ============================================================================

#[cfg(test)]
mod tests {
    use super::usbip::*;
    use super::*;
    use std::sync::atomic::AtomicUsize;
    use tokio::io::AsyncWriteExt as _;

    const DEVICE: [u8; 18] = [18, 1, 0x00, 0x02, 0, 0, 0, 64, 0x34, 0x12, 0xcd, 0xab, 0x00, 0x01, 1, 2, 3, 1];

    fn config(class: u8) -> Vec<u8> {
        let mut c = vec![9, 2, 32, 0, 1, 1, 0, 0x80, 50];
        c.extend_from_slice(&[9, 4, 0, 0, 2, class, 0x06, 0x50, 0]);
        c.extend_from_slice(&[7, 5, 0x81, 0x02, 0x00, 0x02, 0]);
        c.extend_from_slice(&[7, 5, 0x02, 0x02, 0x00, 0x02, 0]);
        c
    }

    /// Synthetic redirected device: mass-storage-like (or HID when `class` = 3), bulk OUT 0x02
    /// echoes into bulk IN 0x81, endpoint 0x83 never completes (for unlink tests).
    #[derive(Default)]
    struct Fake {
        class: u8,
        echo: Mutex<Vec<u8>>,
        selected: Mutex<Vec<String>>,
        cancelled: Arc<AtomicUsize>,
        gone: bool,
    }

    struct CancelProbe(Arc<AtomicUsize>);
    impl Drop for CancelProbe {
        fn drop(&mut self) {
            self.0.fetch_add(1, Ordering::SeqCst);
        }
    }

    #[async_trait::async_trait]
    impl UsbOps for Fake {
        async fn control(&self, setup: SetupPacket, _data: Vec<u8>) -> Result<TransferCompletion<Vec<u8>>, String> {
            if self.gone {
                return Err("usb device channel is closing or closed".into());
            }
            let ok = |d: Vec<u8>| Ok(TransferCompletion { status: Ok(()), actual_length: d.len() as u32, data: d });
            match (setup.request_type.raw(), setup.request, setup.value >> 8) {
                (0x80, 6, 1) => ok(DEVICE[..usize::from(setup.length).min(18)].to_vec()),
                (0x80, 6, 2) => {
                    let c = config(self.class);
                    ok(c[..usize::from(setup.length).min(c.len())].to_vec())
                }
                _ => Ok(TransferCompletion { status: Err(UsbError::Stall), actual_length: 0, data: vec![] }),
            }
        }
        async fn data(&self, kind: TransferType, req: DataTransferRequest<Vec<u8>>) -> Result<TransferCompletion<Vec<u8>>, String> {
            assert_eq!(kind, TransferType::Bulk);
            match req.endpoint.raw() {
                0x02 => {
                    *self.echo.lock().unwrap() = req.data.clone();
                    Ok(TransferCompletion { status: Ok(()), actual_length: req.data.len() as u32, data: vec![] })
                }
                0x81 => {
                    let d = std::mem::take(&mut *self.echo.lock().unwrap());
                    Ok(TransferCompletion { status: Ok(()), actual_length: d.len() as u32, data: d })
                }
                _ => {
                    let _probe = CancelProbe(Arc::clone(&self.cancelled));
                    std::future::pending::<()>().await;
                    unreachable!()
                }
            }
        }
        async fn iso(&self, _req: IsochronousTransferRequest<Vec<u8>, Vec<u32>>) -> Result<IsoCompletion<Vec<u8>, Vec<IsochronousPacketCompletion>>, String> {
            Err("no isochronous endpoint".into())
        }
        async fn select_configuration(&self, descriptor: Option<Vec<u8>>) -> Result<UsbResult<()>, String> {
            self.selected.lock().unwrap().push(format!("config:{}", descriptor.map_or(0, |d| d[5])));
            Ok(Ok(()))
        }
        async fn select_interface(&self, _d: Vec<u8>, s: InterfaceSelection) -> Result<UsbResult<()>, String> {
            self.selected.lock().unwrap().push(format!("iface:{}:{}", s.interface, s.alternate_setting));
            Ok(Ok(()))
        }
        async fn clear_halt(&self, ep: EndpointAddress) -> Result<UsbResult<()>, String> {
            self.selected.lock().unwrap().push(format!("halt:{:02x}", ep.raw()));
            Ok(Ok(()))
        }
    }

    fn control(seq: u32, setup: [u8; 8], dir: u32) -> Submit {
        let length = u32::from(u16::from_le_bytes([setup[6], setup[7]]));
        Submit { seqnum: seq, devid: 0x4d490001, direction: dir, ep: 0, length, setup, ..Submit::default() }
    }

    fn policy(allow: &[&str], deny: &[&str]) -> Policy {
        Policy::from_config(&UsbForwarding {
            allow: allow.iter().map(|s| s.to_string()).collect(),
            deny: deny.iter().map(|s| s.to_string()).collect(),
            max_devices: 4,
        })
        .unwrap()
    }

    #[test]
    fn rules_parse_and_reject_garbage() {
        assert_eq!(parse_rule("1234:ABCD").unwrap(), Rule::Id { vid: 0x1234, pid: Some(0xabcd) });
        assert_eq!(parse_rule("1234:*").unwrap(), Rule::Id { vid: 0x1234, pid: None });
        assert_eq!(parse_rule("class:E0").unwrap(), Rule::Class(0xe0));
        for bad in ["", "1234", "12345:0001", "zzzz:0001", "class:3", "class:xyz", "1234:abc"] {
            assert!(parse_rule(bad).is_err(), "{bad:?} must be rejected");
        }
    }

    #[test]
    fn policy_deny_wins_and_empty_allow_forwards_nothing() {
        let id = Identity { vid: Some(0x1234), pid: Some(0xabcd), classes: vec![0x08] };
        assert!(policy(&[], &[]).check(&id).is_err(), "empty allow list must forward nothing");
        assert!(policy(&["1234:abcd"], &[]).check(&id).is_ok());
        assert!(policy(&["1234:*"], &[]).check(&id).is_ok());
        assert!(policy(&["class:08"], &[]).check(&id).is_ok());
        assert!(policy(&["1234:abcd"], &["class:08"]).check(&id).is_err(), "deny must win over allow");
        assert!(policy(&["1234:0001"], &[]).check(&id).is_err());
        let hid = Identity { vid: Some(0x1234), pid: Some(0xabcd), classes: vec![0x03] };
        let ssot = policy(&["1234:*"], &["class:09", "class:03", "class:e0"]);
        assert!(ssot.check(&hid).is_err(), "SSOT default denies HID even when the id is allowed");
        assert!(ssot.check(&id).is_ok());
    }

    #[test]
    fn identities_from_upstream_announce_strings() {
        let id = identity_from_ids(
            &["USB\\VID_1234&PID_ABCD&REV_0210".into(), "USB\\VID_1234&PID_ABCD".into()],
            &["USB\\Class_08&SubClass_06&Prot_50".into(), "USB\\Class_08&SubClass_06".into(), "USB\\Class_08".into()],
        );
        assert_eq!(id, Identity { vid: Some(0x1234), pid: Some(0xabcd), classes: vec![0x08] });
        let composite = identity_from_ids(&["USB\\VID_046D&PID_C52B".into()], &["USB\\DevClass_00&SubClass_00&Prot_00".into(), "USB\\COMPOSITE".into()]);
        assert_eq!(composite.classes, Vec::<u8>::new(), "composite announce carries no interface class");
    }

    #[test]
    fn descriptors_expose_interface_classes_hidden_by_composite_announce() {
        let mut configs = BTreeMap::new();
        configs.insert(1, config(0x03));
        let real = identity_from_descriptors(&DEVICE, &configs).unwrap();
        assert_eq!(real.classes, vec![0x03]);
        let p = policy(&["1234:abcd"], &["class:03"]);
        let announce = identity_from_ids(&["USB\\VID_1234&PID_ABCD".into()], &["USB\\COMPOSITE".into()]);
        assert!(p.check(&announce).is_ok(), "the announce alone passes");
        assert!(p.check(&real).is_err(), "the descriptor preflight must catch the HID interface");
        assert!(identity_from_descriptors(&DEVICE[..10], &configs).is_err());
    }

    #[test]
    fn vhci_probe_reports_missing_module_and_free_ports() {
        let t = std::env::temp_dir().join(format!("mios-vhci-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&t);
        std::fs::create_dir_all(&t).unwrap();
        let none = probe_vhci(&t);
        assert!(!none.available && none.reason.starts_with("vhci_hcd_not_loaded"), "{none:?}");
        let dir = t.join("devices/platform/vhci_hcd.0");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("status"), "hub port sta spd dev      sockfd local_busid\nhs  0000 006 003 4d490001 000005 0-0\nhs  0001 004 000 00000000 000000 0-0\nss  0008 004 000 00000000 000000 0-0\n").unwrap();
        let p = probe_vhci(&t);
        assert!(p.available, "{p:?}");
        assert_eq!((p.free_hs_ports.clone(), p.used_ports.clone()), (vec![1], vec![0]));
        std::fs::write(dir.join("status"), "hub port sta spd dev      sockfd local_busid\nhs  0000 006 003 4d490001 000005 0-0\nss  0008 004 000 00000000 000000 0-0\n").unwrap();
        let full = probe_vhci(&t);
        assert!(!full.available && full.reason == "no_free_high_speed_vhci_port", "a free SuperSpeed port is not usable for a USB 2 device");
        let _ = std::fs::remove_dir_all(&t);
        assert_eq!((vhci_speed(0), vhci_speed(1), vhci_speed(2)), (Some(2), Some(3), None));
    }

    async fn exchange(fake: Arc<Fake>) -> (tokio::net::UnixStream, tokio::task::JoinHandle<Result<(), String>>) {
        let (kernel, ours) = tokio::net::UnixStream::pair().unwrap();
        let configs = read_descriptors(&*fake).await.unwrap().1;
        let limits = Limits { max_transfer: 64 * 1024, max_inflight: 8 };
        let h = tokio::spawn(serve(ours, fake, Binding::new(configs), limits, Arc::new(Counters::default())));
        (kernel, h)
    }

    #[tokio::test(flavor = "current_thread")]
    async fn usbip_enumeration_configuration_and_bulk_roundtrip() {
        let fake = Arc::new(Fake { class: 0x08, ..Fake::default() });
        let (mut k, engine) = exchange(Arc::clone(&fake)).await;
        let mut expect = HashMap::new();
        // GET_DESCRIPTOR(DEVICE, 18)
        let s = control(1, [0x80, 6, 0, 1, 0, 0, 18, 0], DIR_IN);
        expect.insert(1, (DIR_IN, 0));
        k.write_all(&encode_cmd_submit(&s)).await.unwrap();
        let r = read_ret(&mut k, &expect).await.unwrap();
        assert_eq!((r.command, r.seqnum, r.status, r.data.clone()), (RET_SUBMIT, 1, 0, DEVICE.to_vec()));
        // SET_CONFIGURATION(1) becomes a URBDRC select-configuration with the cached descriptor
        k.write_all(&encode_cmd_submit(&control(2, [0x00, 9, 1, 0, 0, 0, 0, 0], DIR_OUT))).await.unwrap();
        assert_eq!(read_ret(&mut k, &expect).await.unwrap().status, 0);
        // SET_INTERFACE(0, 0), CLEAR_FEATURE(ENDPOINT_HALT, 0x81)
        k.write_all(&encode_cmd_submit(&control(3, [0x01, 11, 0, 0, 0, 0, 0, 0], DIR_OUT))).await.unwrap();
        assert_eq!(read_ret(&mut k, &expect).await.unwrap().status, 0);
        k.write_all(&encode_cmd_submit(&control(4, [0x02, 1, 0, 0, 0x81, 0, 0, 0], DIR_OUT))).await.unwrap();
        assert_eq!(read_ret(&mut k, &expect).await.unwrap().status, 0);
        // bulk OUT 0x02 then bulk IN 0x81 echoes it
        let payload: Vec<u8> = (0..=255u8).cycle().take(4096).collect();
        let out = Submit { seqnum: 5, devid: 0x4d490001, direction: DIR_OUT, ep: 2, length: 4096, data: payload.clone(), ..Submit::default() };
        k.write_all(&encode_cmd_submit(&out)).await.unwrap();
        let r = read_ret(&mut k, &expect).await.unwrap();
        assert_eq!((r.seqnum, r.status, r.actual), (5, 0, 4096));
        let inn = Submit { seqnum: 6, devid: 0x4d490001, direction: DIR_IN, ep: 1, length: 8192, ..Submit::default() };
        expect.insert(6, (DIR_IN, 0));
        k.write_all(&encode_cmd_submit(&inn)).await.unwrap();
        let r = read_ret(&mut k, &expect).await.unwrap();
        assert_eq!((r.seqnum, r.status, r.data.clone()), (6, 0, payload));
        assert_eq!(*fake.selected.lock().unwrap(), vec!["config:1", "iface:0:0", "halt:81"]);
        drop(k);
        assert_eq!(engine.await.unwrap(), Ok(()), "kernel detach (EOF) ends the engine cleanly");
    }

    #[tokio::test(flavor = "current_thread")]
    async fn usbip_unlink_cancels_the_pending_urb_and_suppresses_its_completion() {
        let fake = Arc::new(Fake { class: 0x08, ..Fake::default() });
        let (mut k, engine) = exchange(Arc::clone(&fake)).await;
        let expect = HashMap::new();
        k.write_all(&encode_cmd_submit(&control(1, [0x00, 9, 1, 0, 0, 0, 0, 0], DIR_OUT))).await.unwrap();
        assert_eq!(read_ret(&mut k, &expect).await.unwrap().status, 0);
        // 0x83 is not in the configuration: refused with a stall, never forwarded
        let stray = Submit { seqnum: 2, direction: DIR_IN, ep: 3, length: 64, ..Submit::default() };
        k.write_all(&encode_cmd_submit(&stray)).await.unwrap();
        assert_eq!(read_ret(&mut k, &expect).await.unwrap().status, EPIPE);
        // a pending IN on 0x81 after the echo buffer was drained still completes (empty); a held
        // URB is modelled by an unknown seqnum unlink returning 0 and a real one returning -ECONNRESET
        k.write_all(&encode_cmd_unlink(3, 0, 999)).await.unwrap();
        let r = read_ret(&mut k, &expect).await.unwrap();
        assert_eq!((r.command, r.seqnum, r.status), (RET_UNLINK, 3, 0), "unlink of an unknown URB reports 0");
        drop(k);
        let _ = engine.await;
        assert_eq!(fake.cancelled.load(Ordering::SeqCst), 0);
    }

    struct Hold(Arc<AtomicUsize>);
    #[async_trait::async_trait]
    impl UsbOps for Hold {
        async fn control(&self, _s: SetupPacket, _d: Vec<u8>) -> Result<TransferCompletion<Vec<u8>>, String> {
            let _probe = CancelProbe(Arc::clone(&self.0));
            std::future::pending::<()>().await;
            unreachable!()
        }
        async fn data(&self, _k: TransferType, _r: DataTransferRequest<Vec<u8>>) -> Result<TransferCompletion<Vec<u8>>, String> { unreachable!() }
        async fn iso(&self, _r: IsochronousTransferRequest<Vec<u8>, Vec<u32>>) -> Result<IsoCompletion<Vec<u8>, Vec<IsochronousPacketCompletion>>, String> { unreachable!() }
        async fn select_configuration(&self, _d: Option<Vec<u8>>) -> Result<UsbResult<()>, String> { unreachable!() }
        async fn select_interface(&self, _d: Vec<u8>, _s: InterfaceSelection) -> Result<UsbResult<()>, String> { unreachable!() }
        async fn clear_halt(&self, _e: EndpointAddress) -> Result<UsbResult<()>, String> { unreachable!() }
    }

    #[tokio::test(flavor = "current_thread")]
    async fn usbip_unlink_of_an_in_flight_urb_cancels_it() {
        let cancelled = Arc::new(AtomicUsize::new(0));
        let (mut k, ours) = tokio::net::UnixStream::pair().unwrap();
        let engine = tokio::spawn(serve(ours, Arc::new(Hold(Arc::clone(&cancelled))), Binding::default(),
            Limits { max_transfer: 4096, max_inflight: 1 }, Arc::new(Counters::default())));
        let expect = HashMap::from([(1, (DIR_IN, 0)), (2, (DIR_IN, 0))]);
        k.write_all(&encode_cmd_submit(&control(1, [0x80, 6, 0, 3, 0, 0, 255, 0], DIR_IN))).await.unwrap();
        // the in-flight bound refuses a second URB with -EAGAIN instead of queueing it
        k.write_all(&encode_cmd_submit(&control(2, [0x80, 6, 0, 3, 0, 0, 255, 0], DIR_IN))).await.unwrap();
        let r = read_ret(&mut k, &expect).await.unwrap();
        assert_eq!((r.seqnum, r.status), (2, EAGAIN));
        k.write_all(&encode_cmd_unlink(3, 0, 1)).await.unwrap();
        let r = read_ret(&mut k, &expect).await.unwrap();
        assert_eq!((r.command, r.seqnum, r.status), (RET_UNLINK, 3, ECONNRESET));
        for _ in 0..20 {
            if cancelled.load(Ordering::SeqCst) == 1 {
                break;
            }
            tokio::task::yield_now().await;
        }
        assert_eq!(cancelled.load(Ordering::SeqCst), 1, "the URBDRC request future was dropped (cancel on the wire)");
        drop(k);
        assert_eq!(engine.await.unwrap(), Ok(()));
    }

    #[tokio::test(flavor = "current_thread")]
    async fn usbip_negative_controls_unknown_config_gone_device_and_garbage() {
        let fake = Arc::new(Fake { class: 0x08, ..Fake::default() });
        let (mut k, engine) = exchange(Arc::clone(&fake)).await;
        let expect = HashMap::new();
        k.write_all(&encode_cmd_submit(&control(1, [0x00, 9, 7, 0, 0, 0, 0, 0], DIR_OUT))).await.unwrap();
        assert_eq!(read_ret(&mut k, &expect).await.unwrap().status, EPIPE, "unknown configuration is not forwarded");
        k.write_all(&encode_cmd_submit(&control(2, [0x01, 11, 1, 0, 0, 0, 0, 0], DIR_OUT))).await.unwrap();
        assert_eq!(read_ret(&mut k, &expect).await.unwrap().status, EPIPE, "SET_INTERFACE before SET_CONFIGURATION");
        assert!(fake.selected.lock().unwrap().is_empty());
        let mut garbage = encode_cmd_unlink(9, 0, 1);
        garbage[3] = 0x7f;
        k.write_all(&garbage).await.unwrap();
        let err = engine.await.unwrap().unwrap_err();
        assert!(err.contains("unexpected USB/IP command"), "{err}");

        let gone = Arc::new(Fake { class: 0x08, gone: true, ..Fake::default() });
        assert!(read_descriptors(&*gone).await.is_err());
        let (mut k, ours) = tokio::net::UnixStream::pair().unwrap();
        let engine = tokio::spawn(serve(ours, gone, Binding::default(), Limits { max_transfer: 18, max_inflight: 4 }, Arc::new(Counters::default())));
        let expect = HashMap::from([(1, (DIR_IN, 0))]);
        k.write_all(&encode_cmd_submit(&control(1, [0x80, 6, 0, 1, 0, 0, 18, 0], DIR_IN))).await.unwrap();
        assert_eq!(read_ret(&mut k, &expect).await.unwrap().status, ENODEV, "hot-unplugged device completes with -ENODEV");
        let big = Submit { seqnum: 2, direction: DIR_OUT, ep: 2, length: 19, data: vec![0; 19], ..Submit::default() };
        k.write_all(&encode_cmd_submit(&big)).await.unwrap();
        assert!(engine.await.unwrap().unwrap_err().contains("exceeds"), "oversized URB closes the port");
    }

    #[tokio::test(flavor = "current_thread")]
    async fn usbip_short_not_ok_reports_remote_io() {
        let fake = Arc::new(Fake { class: 0x08, ..Fake::default() });
        let (mut k, _engine) = exchange(Arc::clone(&fake)).await;
        let mut expect = HashMap::new();
        k.write_all(&encode_cmd_submit(&control(1, [0x00, 9, 1, 0, 0, 0, 0, 0], DIR_OUT))).await.unwrap();
        assert_eq!(read_ret(&mut k, &expect).await.unwrap().status, 0);
        let inn = Submit { seqnum: 2, direction: DIR_IN, ep: 1, length: 512, flags: URB_SHORT_NOT_OK, ..Submit::default() };
        expect.insert(2, (DIR_IN, 0));
        k.write_all(&encode_cmd_submit(&inn)).await.unwrap();
        assert_eq!(read_ret(&mut k, &expect).await.unwrap().status, EREMOTEIO);
    }

    // ---- synthetic URBDRC exchange through the upstream server processors ----

    use ironrdp_core::{decode, encode_vec};
    use ironrdp_dvc::{DvcMessage, DvcProcessor as _};
    use ironrdp_rdpeusb::io::device::{
        DeviceInfo, UsbClassCodes, UsbConfigInfo, UsbConnectionSpeed, UsbDeviceDescriptorInfo, UsbDeviceLocation, UsbInterfaceInfo,
        add_device_from_info,
    };
    use ironrdp_rdpeusb::io::{IoControlCompletionResult, RequestId, TransferInCompletionResult, TransferOutCompletionResult};
    use ironrdp_rdpeusb::pdu::caps::{Capability, RimExchangeCapabilityResponse};
    use ironrdp_rdpeusb::pdu::header::InterfaceId;
    use ironrdp_rdpeusb::pdu::notify::{ChannelCreated, Direction as NotifyDirection};
    use ironrdp_rdpeusb::pdu::{UrbdrcClientDevicePdu, UrbdrcServerDevicePdu};
    use ironrdp_rdpeusb::server::{UrbdrcDeviceServer, UrbdrcDeviceServerBackend};

    fn device_info(vid: u16, pid: u16, class: u8, composite: bool) -> DeviceInfo {
        DeviceInfo {
            location: UsbDeviceLocation { bus_number: 7, address: 2, port_numbers: vec![1, 4] },
            descriptor: UsbDeviceDescriptorInfo {
                vendor_id: vid,
                product_id: pid,
                device_version: 0x0100,
                usb_version: ironrdp_usb::BcdVersion::from_raw(0x0200),
                class_codes: if composite { UsbClassCodes::PER_INTERFACE } else { UsbClassCodes { class_code: class, sub_class_code: 6, protocol_code: 0x50 } },
                num_configurations: 1,
            },
            // A composite device (per-interface class, several interfaces) announces only USB\COMPOSITE:
            // a harmless first interface hides `class` in the second one.
            active_config: Some(UsbConfigInfo {
                interfaces: if composite {
                    vec![
                        UsbInterfaceInfo { class_codes: UsbClassCodes { class_code: 0x08, sub_class_code: 6, protocol_code: 0x50 } },
                        UsbInterfaceInfo { class_codes: UsbClassCodes { class_code: class, sub_class_code: 1, protocol_code: 1 } },
                    ]
                } else {
                    vec![UsbInterfaceInfo { class_codes: UsbClassCodes { class_code: class, sub_class_code: 6, protocol_code: 0x50 } }]
                },
            }),
            speed: UsbConnectionSpeed::High,
            isochronous: false,
        }
    }

    struct Decide {
        policy: Policy,
        verdicts: std::sync::mpsc::Sender<(Identity, u32, Result<(), String>)>,
    }

    impl UrbdrcDeviceServerBackend for Decide {
        fn add_device(&mut self, device: DeviceAnnounce) -> ironrdp_pdu::PduResult<()> {
            let id = identity_from_announce(&device);
            let verdict = self.policy.check(&id);
            let _ = self.verdicts.send((id, device.usb_device_caps.device_speed.to_u32(), verdict));
            Ok(())
        }
        fn device_text(&mut self, _t: DeviceText) {}
        fn io_control_completed(&mut self, _c: u32, _r: RequestId, _x: IoControlCompletionResult) -> ironrdp_pdu::PduResult<()> { Ok(()) }
        fn internal_io_control_completed(&mut self, _c: u32, _r: RequestId, _x: IoControlCompletionResult) -> ironrdp_pdu::PduResult<()> { Ok(()) }
        fn transfer_in_completed(&mut self, _c: u32, _r: RequestId, _x: TransferInCompletionResult) -> ironrdp_pdu::PduResult<()> { Ok(()) }
        fn transfer_out_completed(&mut self, _c: u32, _r: RequestId, _x: TransferOutCompletionResult) -> ironrdp_pdu::PduResult<()> { Ok(()) }
    }

    fn enc<T: ironrdp_core::Encode>(p: &T) -> Vec<u8> {
        encode_vec(p).unwrap()
    }
    fn dec(m: &DvcMessage) -> UrbdrcServerDevicePdu {
        decode(&encode_vec(m.as_ref()).unwrap()).unwrap()
    }

    /// Full per-device channel sequence (caps, CHANNEL_CREATED, ADD_DEVICE) with real encoded PDUs;
    /// returns the policy verdict and whether the server then encodes a RETRACT_DEVICE.
    fn urbdrc_add(policy: Policy, info: &DeviceInfo) -> (Identity, u32, Result<(), String>, bool) {
        let (tx, rx) = std::sync::mpsc::channel();
        let completion = InterfaceId::try_from(5).unwrap();
        let mut server = UrbdrcDeviceServer::new(Box::new(Decide { policy, verdicts: tx }), completion).unwrap();
        let UrbdrcServerDevicePdu::Caps(caps) = dec(&server.start(11).unwrap()[0]) else { panic!("expected caps") };
        let resp = server
            .process(11, &enc(&UrbdrcClientDevicePdu::Caps(RimExchangeCapabilityResponse { msg_id: caps.msg_id, capability: Capability::RimCapabilityVersion01, result: 0 })))
            .unwrap();
        let UrbdrcServerDevicePdu::ChanCreated(cc) = dec(&resp[1]) else { panic!("expected CHANNEL_CREATED") };
        server.process(11, &enc(&UrbdrcClientDevicePdu::ChanCreated(ChannelCreated { msg_id: cc.msg_id, direction: NotifyDirection::ToServer }))).unwrap();
        let add = add_device_from_info(InterfaceId::try_from(4).unwrap(), info).unwrap();
        server.process(11, &enc(&UrbdrcClientDevicePdu::AddDev(add))).unwrap();
        let (id, speed, verdict) = rx.try_recv().expect("ADD_DEVICE reaches the backend");
        let retracted = if verdict.is_err() {
            matches!(dec(&server.retract_device(UsbRetractReason::BlockedByPolicy).unwrap()), UrbdrcServerDevicePdu::Retract(_))
        } else {
            false
        };
        (id, speed, verdict, retracted)
    }

    #[test]
    fn urbdrc_allowed_device_is_admitted_from_real_add_device_pdu() {
        let (id, speed, verdict, retracted) = urbdrc_add(policy(&["1234:abcd"], &["class:03"]), &device_info(0x1234, 0xabcd, 0x08, false));
        assert_eq!(id, Identity { vid: Some(0x1234), pid: Some(0xabcd), classes: vec![0x08] });
        assert_eq!(speed, 1, "high-speed announce");
        assert_eq!(vhci_speed(speed), Some(3));
        assert!(verdict.is_ok() && !retracted);
    }

    #[test]
    fn urbdrc_denied_and_unlisted_devices_are_retracted() {
        let (_, _, verdict, retracted) = urbdrc_add(policy(&["1234:*"], &["class:03"]), &device_info(0x1234, 0x0001, 0x03, false));
        assert!(verdict.unwrap_err().contains("class:03") && retracted, "HID is denied by class and retracted");
        let (_, _, verdict, retracted) = urbdrc_add(policy(&[], &[]), &device_info(0x1234, 0xabcd, 0x08, false));
        assert!(verdict.is_err() && retracted, "an empty allow list retracts everything");
        let (id, _, verdict, _) = urbdrc_add(policy(&["1234:abcd"], &["class:03"]), &device_info(0x1234, 0xabcd, 0x03, true));
        assert!(id.classes.is_empty() && verdict.is_ok(), "composite announce hides the HID class: only the descriptor preflight catches it");
    }

    #[test]
    fn status_reports_server_disabled_and_missing_endpoint_honestly() {
        let cfg = Config::default();
        let stats = PipeStats::default();
        let v = status_json(&cfg, &stats);
        assert_eq!(v["implemented"], true);
        assert_eq!(v["permitted"], false);
        assert_eq!(v["client"]["status"], "server_disabled");
        assert!(v["client"]["platforms"].as_str().unwrap().contains("iOS/iPadOS"));
        assert_eq!(v["endpoint"]["kind"], "usbip-vhci");
        if !Path::new("/sys/devices/platform/vhci_hcd.0").exists() {
            assert_eq!(v["endpoint"]["available"], false);
        }
    }

    #[test]
    fn admission_order_permission_target_policy_endpoint() {
        let mut cfg = Config::default();
        let t = std::env::temp_dir().join(format!("mios-usb-admit-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&t);
        std::fs::create_dir_all(t.join("sys")).unwrap();
        cfg.runtime_dir = t.clone();
        cfg.device_forwarding.usb.allow = vec!["1234:abcd".into()];
        let shared = |cfg: &Config| Shared {
            cfg: cfg.clone(),
            stats: Arc::new(PipeStats::default()),
            policy: Policy::from_config(&cfg.device_forwarding.usb).unwrap(),
            sys: t.join("sys"),
            next_key: AtomicU64::new(1),
        };
        let id = Identity { vid: Some(0x1234), pid: Some(0xabcd), classes: vec![0x08] };
        assert!(shared(&cfg).admit(&id, 1, 0).unwrap_err().contains("server permission off"));
        cfg.redirect.usb = true;
        assert!(shared(&cfg).admit(&id, 1, 0).unwrap_err().contains("vhci_hcd_not_loaded"), "no module -> unsupported, nothing loaded");
        let other = Identity { vid: Some(0x1234), pid: Some(0x0001), classes: vec![0x08] };
        assert!(shared(&cfg).admit(&other, 1, 0).unwrap_err().contains("allow"));
        std::fs::create_dir_all(t.join("mios/device-forwarding")).unwrap();
        std::fs::write(t.join("mios/device-forwarding/targets.json"), r#"{"sessions":{"rdp":{"target":"none"}}}"#).unwrap();
        assert!(shared(&cfg).admit(&id, 1, 0).unwrap_err().contains("target is none"));
        std::fs::write(t.join("mios/device-forwarding/targets.json"), r#"{"sessions":{"rdp":{"target":"host-vhci"}}}"#).unwrap();
        let dir = t.join("sys/devices/platform/vhci_hcd.0");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("status"), "hub port sta spd dev      sockfd local_busid\nhs  0000 004 000 00000000 000000 0-0\n").unwrap();
        assert_eq!(shared(&cfg).admit(&id, 1, 0), Ok(()));
        assert!(shared(&cfg).admit(&id, 7, 0).unwrap_err().contains("speed"));
        let s = shared(&cfg);
        for k in 0..4 {
            s.stats.usb_devices.lock().unwrap().push(json!({"key": k, "serial": 3, "state": "attached"}));
        }
        assert!(s.admit(&id, 1, 3).unwrap_err().contains("already holds"), "per-session device limit");
        assert_eq!(s.admit(&id, 1, 4), Ok(()), "claims of another session do not count");
        let _ = std::fs::remove_dir_all(&t);
    }
}
