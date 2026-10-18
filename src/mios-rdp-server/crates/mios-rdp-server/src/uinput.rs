//! Minimal /dev/uinput devices for remote touch (multitouch protocol B) and pen (tablet tool).
//! Each device exists only while its RDPEI channel is open and is destroyed on drop.
//!
//! Unsafe: the uinput ioctl ABI only.
#![allow(unsafe_code)]

use std::fs::{File, OpenOptions};
use std::io::Write as _;
use std::os::fd::AsRawFd;
use std::os::unix::fs::OpenOptionsExt;

use anyhow::{Context as _, Result, bail};

pub const EV_SYN: u16 = 0x00;
pub const EV_KEY: u16 = 0x01;
pub const EV_ABS: u16 = 0x03;
pub const SYN_REPORT: u16 = 0;
pub const BTN_TOOL_PEN: u16 = 0x140;
pub const BTN_TOOL_RUBBER: u16 = 0x141;
pub const BTN_TOUCH: u16 = 0x14a;
pub const BTN_STYLUS: u16 = 0x14b;
pub const BTN_TOOL_FINGER: u16 = 0x145;
pub const BTN_TOOL_DOUBLETAP: u16 = 0x14d;
pub const ABS_X: u16 = 0x00;
pub const ABS_Y: u16 = 0x01;
pub const ABS_PRESSURE: u16 = 0x18;
pub const ABS_TILT_X: u16 = 0x1a;
pub const ABS_TILT_Y: u16 = 0x1b;
pub const ABS_MT_SLOT: u16 = 0x2f;
pub const ABS_MT_POSITION_X: u16 = 0x35;
pub const ABS_MT_POSITION_Y: u16 = 0x36;
pub const ABS_MT_TRACKING_ID: u16 = 0x39;
pub const INPUT_PROP_DIRECT: u16 = 0x01;
pub const BUS_VIRTUAL: u16 = 0x06;

const UI_SET_EVBIT: libc::c_ulong = 0x4004_5564;
const UI_SET_KEYBIT: libc::c_ulong = 0x4004_5565;
const UI_SET_ABSBIT: libc::c_ulong = 0x4004_5567;
const UI_SET_PROPBIT: libc::c_ulong = 0x4004_556e;
const UI_DEV_SETUP: libc::c_ulong = 0x405c_5503;
const UI_ABS_SETUP: libc::c_ulong = 0x401c_5504;
const UI_DEV_CREATE: libc::c_ulong = 0x5501;
const UI_DEV_DESTROY: libc::c_ulong = 0x5502;

#[repr(C)]
struct UinputSetup {
    bustype: u16,
    vendor: u16,
    product: u16,
    version: u16,
    name: [u8; 80],
    ff_effects_max: u32,
}

#[repr(C)]
struct AbsInfo {
    value: i32,
    minimum: i32,
    maximum: i32,
    fuzz: i32,
    flat: i32,
    resolution: i32,
}

#[repr(C)]
struct UinputAbsSetup {
    code: u16,
    _pad: u16,
    absinfo: AbsInfo,
}

#[repr(C)]
struct InputEvent {
    sec: i64,
    usec: i64,
    kind: u16,
    code: u16,
    value: i32,
}

pub struct Axis {
    pub code: u16,
    pub min: i32,
    pub max: i32,
    pub resolution: i32,
}

pub struct Device {
    file: File,
    pub name: String,
    buf: Vec<u8>,
}

fn ioctl_int(fd: i32, req: libc::c_ulong, v: libc::c_int) -> Result<()> {
    // SAFETY: integer-argument uinput ioctl on an open /dev/uinput fd.
    if unsafe { libc::ioctl(fd, req as _, v) } < 0 {
        bail!("uinput ioctl {req:#x}({v}): {}", std::io::Error::last_os_error());
    }
    Ok(())
}

impl Device {
    pub fn create(name: &str, keys: &[u16], axes: &[Axis], direct: bool) -> Result<Self> {
        let file = OpenOptions::new()
            .write(true)
            .custom_flags(libc::O_NONBLOCK | libc::O_CLOEXEC)
            .open("/dev/uinput")
            .context("open /dev/uinput")?;
        let fd = file.as_raw_fd();
        if !keys.is_empty() {
            ioctl_int(fd, UI_SET_EVBIT, i32::from(EV_KEY))?;
            for k in keys {
                ioctl_int(fd, UI_SET_KEYBIT, i32::from(*k))?;
            }
        }
        if !axes.is_empty() {
            ioctl_int(fd, UI_SET_EVBIT, i32::from(EV_ABS))?;
            for a in axes {
                ioctl_int(fd, UI_SET_ABSBIT, i32::from(a.code))?;
                let s = UinputAbsSetup {
                    code: a.code,
                    _pad: 0,
                    absinfo: AbsInfo { value: 0, minimum: a.min, maximum: a.max, fuzz: 0, flat: 0, resolution: a.resolution },
                };
                // SAFETY: struct uinput_abs_setup per linux/uinput.h.
                if unsafe { libc::ioctl(fd, UI_ABS_SETUP as _, &s) } < 0 {
                    bail!("UI_ABS_SETUP {}: {}", a.code, std::io::Error::last_os_error());
                }
            }
        }
        if direct {
            ioctl_int(fd, UI_SET_PROPBIT, i32::from(INPUT_PROP_DIRECT))?;
        }
        let mut setup = UinputSetup { bustype: BUS_VIRTUAL, vendor: 0x4d69, product: 0x4f53, version: 1, name: [0; 80], ff_effects_max: 0 };
        let n = name.as_bytes();
        setup.name[..n.len().min(79)].copy_from_slice(&n[..n.len().min(79)]);
        // SAFETY: struct uinput_setup per linux/uinput.h.
        unsafe {
            if libc::ioctl(fd, UI_DEV_SETUP as _, &setup) < 0 {
                bail!("UI_DEV_SETUP: {}", std::io::Error::last_os_error());
            }
            if libc::ioctl(fd, UI_DEV_CREATE as _) < 0 {
                bail!("UI_DEV_CREATE: {}", std::io::Error::last_os_error());
            }
        }
        Ok(Self { file, name: name.to_owned(), buf: Vec::with_capacity(24 * 16) })
    }

    pub fn push(&mut self, kind: u16, code: u16, value: i32) {
        let ev = InputEvent { sec: 0, usec: 0, kind, code, value };
        // SAFETY: plain-old-data struct viewed as bytes.
        let bytes = unsafe { std::slice::from_raw_parts((&ev as *const InputEvent).cast::<u8>(), size_of::<InputEvent>()) };
        self.buf.extend_from_slice(bytes);
    }

    /// Write the queued events followed by SYN_REPORT.
    pub fn sync(&mut self) -> Result<()> {
        self.push(EV_SYN, SYN_REPORT, 0);
        let r = self.file.write_all(&self.buf);
        self.buf.clear();
        r.context("uinput write")
    }
}

impl Drop for Device {
    fn drop(&mut self) {
        tracing::debug!(device = %self.name, "uinput device destroyed");
        // SAFETY: destroy the device created on this fd.
        unsafe {
            libc::ioctl(self.file.as_raw_fd(), UI_DEV_DESTROY as _);
        }
    }
}

/// Hyprland's device name for a libinput device: lower case, spaces -> '-'.
pub fn hypr_name(name: &str) -> String {
    name.to_lowercase().replace(' ', "-")
}

#[cfg(test)]
mod tests {
    #[test]
    fn struct_sizes_match_the_kernel_abi() {
        assert_eq!(size_of::<super::UinputSetup>(), 92);
        assert_eq!(size_of::<super::UinputAbsSetup>(), 28);
        assert_eq!(size_of::<super::InputEvent>(), 24);
        assert_eq!(super::hypr_name("MiOS Remote Touch"), "mios-remote-touch");
    }
}
