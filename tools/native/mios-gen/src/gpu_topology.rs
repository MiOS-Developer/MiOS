// AI-hint: GPU topology for the desktop hardware profiles: which DRM cards and render nodes this boot has (sysfs),
// which GPU a profile names ([desktop.hyprland.hardware_profiles.<p>].pci / pci_file), the render node a nested
// session uses ([desktop.nested].render_node + the running profile's nested_render; wl_device_proxy.rs steers the
// nested Hyprland there, because Aquamarine's Wayland backend opens the parent's linux-dmabuf main device and has no
// node selector) and the read-only boot display report (`mios-gen boot-display-report`: fbcon / fb / DRM driver order for this boot).
// AI-related: usr/share/mios/mios.toml [desktop.hyprland.hardware_profiles], [desktop.nested], [units."mios-boot-display-report.service"],
//   tools/native/mios-gen/src/nested_session.rs, tools/native/mios-gen/src/greeter.rs, tools/native/mios-gen/src/render.rs (hyprland-session),
//   usr/share/doc/mios/desktop-gpu-routing.md

use serde_json::{json, Value as Json};
use std::{
    fs,
    io::Read,
    path::{Path, PathBuf},
};
use toml::Value;

/// One DRM device of this boot: its KMS card, its render node (if any), PCI identity and kernel driver.
#[derive(Debug, Clone, PartialEq)]
pub struct Gpu {
    pub card: PathBuf,
    pub render: Option<PathBuf>,
    pub pci: String,
    pub driver: String,
    pub boot_vga: bool,
}

impl Gpu {
    pub fn to_json(&self) -> Json {
        json!({"card": self.card, "render": self.render, "pci": self.pci, "driver": self.driver, "boot_vga": self.boot_vga})
    }
    /// Mesa's DRI_PRIME selector for this device.
    pub fn dri_prime(&self) -> String {
        format!("pci-{}", self.pci.replace([':', '.'], "_"))
    }
}

/// Where the device nodes and their sysfs class live; tests point both at fixtures.
pub struct Host<'a> {
    pub dri: &'a Path,
    pub sys: &'a Path,
}

pub fn host() -> Host<'static> {
    Host {
        dri: Path::new("/dev/dri"),
        sys: Path::new("/sys/class/drm"),
    }
}

fn numbered(name: &str, prefix: &str) -> Option<u32> {
    name.strip_prefix(prefix)
        .filter(|s| !s.is_empty() && s.bytes().all(|c| c.is_ascii_digit()))
        .and_then(|s| s.parse().ok())
}

fn link_name(p: &Path) -> String {
    fs::canonicalize(p)
        .ok()
        .and_then(|p| p.file_name().map(|s| s.to_string_lossy().into_owned()))
        .unwrap_or_default()
}

pub fn valid_pci(pci: &str) -> bool {
    pci.len() == 12
        && pci.bytes().enumerate().all(|(i, c)| match i {
            4 | 7 => c == b':',
            10 => c == b'.',
            _ => c.is_ascii_hexdigit(),
        })
}

/// Every DRM card of this boot, in card order, with its render node matched by PCI device.
pub fn inventory(h: &Host) -> Vec<Gpu> {
    let mut cards = Vec::new();
    let mut renders = Vec::new();
    for e in fs::read_dir(h.sys).into_iter().flatten().flatten() {
        let name = e.file_name().to_string_lossy().into_owned();
        let device = h.sys.join(&name).join("device");
        if let Some(n) = numbered(&name, "card") {
            cards.push((n, name, device));
        } else if let Some(n) = numbered(&name, "renderD") {
            renders.push((n, name, link_name(&device)));
        }
    }
    cards.sort();
    renders.sort();
    cards
        .into_iter()
        .map(|(_, name, device)| {
            let pci = link_name(&device);
            let render = renders
                .iter()
                .find(|(_, _, p)| !p.is_empty() && *p == pci)
                .map(|(_, r, _)| h.dri.join(r));
            Gpu {
                card: h.dri.join(&name),
                render,
                driver: link_name(&device.join("driver")),
                boot_vga: fs::read_to_string(device.join("boot_vga"))
                    .is_ok_and(|s| s.trim() == "1"),
                pci,
            }
        })
        .collect()
}

fn profiles(data: &Value) -> Option<&toml::Table> {
    data.get("desktop")?
        .get("hyprland")?
        .get("hardware_profiles")?
        .as_table()
}

/// The PCI identity a hardware profile names: its `pci`, else the host file `pci_file` under `root`.
pub fn profile_pci(root: &Path, data: &Value, profile: &str) -> Option<String> {
    let cfg = profiles(data)?.get(profile)?;
    let pci = cfg
        .get("pci")
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .or_else(|| {
            let file = cfg.get("pci_file").and_then(Value::as_str)?;
            if !file.starts_with('/')
                || Path::new(file)
                    .components()
                    .any(|c| c == std::path::Component::ParentDir)
            {
                return None;
            }
            fs::read_to_string(root.join(file.trim_start_matches('/')))
                .ok()
                .map(|s| s.trim().to_string())
        })?;
    valid_pci(&pci).then_some(pci)
}

/// The GPU of this boot that a hardware profile names, if it is bound to a DRM driver now.
pub fn profile_gpu(gpus: &[Gpu], root: &Path, data: &Value, profile: &str) -> Option<Gpu> {
    let pci = profile_pci(root, data, profile)?;
    gpus.iter().find(|g| g.pci == pci).cloned()
}

/// GPU hand-off: the boot profile's GPU is not bound to a DRM driver right now (handed to a VM) and the profile names
/// `handoff_fallback`; the desktop then runs that fallback profile in place, without a reboot.
pub fn handoff_profile(data: &Value, root: &Path, booted: &str, gpus: &[Gpu]) -> Option<String> {
    let fallback = profiles(data)?
        .get(booted)?
        .get("handoff_fallback")?
        .as_str()?;
    if fallback == booted
        || !profiles(data)?.contains_key(fallback)
        || profile_pci(root, data, booted).is_none()
    {
        return None;
    }
    (profile_gpu(gpus, root, data, booted).is_none()
        && profile_gpu(gpus, root, data, fallback).is_some())
    .then(|| fallback.to_string())
}

/// The hardware profile the running desktop uses: the parent session's MIOS_DESKTOP_HARDWARE_PROFILE export,
/// then the boot entry's mios.desktop.gpu=, then [desktop.hyprland].hardware_profile.
pub fn running_profile(
    data: &Value,
    exported: Option<&str>,
    cmdline: &str,
) -> Result<String, String> {
    crate::render::render_hyprland::profile_name(data, exported.filter(|s| !s.is_empty()), cmdline)
}

const MESA_EGL_VENDOR: &str = "/usr/share/glvnd/egl_vendor.d/50_mesa.json";

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum RenderMode {
    /// No steering: the nested compositor opens whatever main device the parent announces (the old behaviour).
    Inherit,
    /// The nested compositor and its clients render on one chosen render node.
    Device,
    /// Mesa software rasterisation (llvmpipe) for the nested compositor and its clients.
    Software,
}

#[derive(Debug, Clone, PartialEq)]
pub struct RenderChoice {
    /// [desktop.nested].render_node as configured ("auto" or an explicit override).
    pub policy: String,
    /// The running hardware profile the choice was made for.
    pub profile: String,
    /// The rule after "auto" was expanded (inherit | software | <profile id> | /dev/dri/renderD<N>).
    pub rule: String,
    pub mode: RenderMode,
    pub gpu: Option<Gpu>,
    /// Set when "auto" could not be honoured on this boot and the session inherits instead.
    pub degraded: Option<String>,
}

fn rule_ok(data: &Value, rule: &str) -> bool {
    matches!(rule, "inherit" | "software")
        || profiles(data).is_some_and(|p| p.contains_key(rule))
        || rule
            .strip_prefix("/dev/dri/")
            .and_then(|n| numbered(n, "renderD"))
            .is_some()
}

/// The render policy of a nested session: [desktop.nested].render_node ("auto" = the running profile's
/// [desktop.hyprland.hardware_profiles.<p>].nested_render). "auto" degrades to "inherit" (recorded) when the GPU it
/// names is absent this boot; an explicit override that cannot be met is an error.
pub fn nested_render(
    data: &Value,
    root: &Path,
    profile: &str,
    gpus: &[Gpu],
) -> Result<RenderChoice, String> {
    let policy = match data
        .get("desktop")
        .and_then(|d| d.get("nested"))
        .and_then(|n| n.get("render_node"))
    {
        None => "auto".to_string(),
        Some(v) => v
            .as_str()
            .ok_or("desktop.nested.render_node must be a string")?
            .to_string(),
    };
    if policy != "auto" && !rule_ok(data, &policy) {
        return Err(format!("desktop.nested.render_node {policy:?} must be auto, inherit, software, a hardware profile id or /dev/dri/renderD<N>"));
    }
    let auto = policy == "auto";
    let rule = if auto {
        let key = format!("desktop.hyprland.hardware_profiles.{profile}.nested_render");
        match profiles(data)
            .and_then(|p| p.get(profile))
            .and_then(|c| c.get("nested_render"))
        {
            None => "inherit".to_string(),
            Some(v) => {
                let r = v.as_str().ok_or(format!("{key} must be a string"))?;
                if !rule_ok(data, r) {
                    return Err(format!("{key} {r:?} must be inherit, software, a hardware profile id or /dev/dri/renderD<N>"));
                }
                r.to_string()
            }
        }
    } else {
        policy.clone()
    };
    let mut choice = RenderChoice {
        policy,
        profile: profile.into(),
        rule: rule.clone(),
        mode: RenderMode::Inherit,
        gpu: None,
        degraded: None,
    };
    let found = match rule.as_str() {
        "inherit" => return Ok(choice),
        "software" => {
            choice.mode = RenderMode::Software;
            return Ok(choice);
        }
        r if r.starts_with('/') => gpus.iter().find(|g| g.render.as_deref() == Some(Path::new(r))).cloned()
            .ok_or_else(|| format!("render node {r} is not a DRM render node of this boot")),
        id => match profile_gpu(gpus, root, data, id) {
            Some(g) if g.render.is_some() => Ok(g),
            Some(g) => Err(format!("the {id} GPU {} has no render node", g.pci)),
            None => Err(format!("the {id} GPU is not bound to a DRM driver this boot (assigned to a VM, or its PCI identity is unset)")),
        },
    };
    // The parent composites the nested session's buffers with its own GPU's driver. Measured on Hyprland 0.56 with an
    // NVIDIA parent: it lists no LINEAR modifier and shows nothing for iGPU-rendered buffers (tiled ones fail
    // eglCreateImageKHR), so "auto" only steers to a GPU of the parent's driver family unless
    // [desktop.nested].cross_vendor_present = true. An explicit override is honoured as given.
    let cross_ok = match data
        .get("desktop")
        .and_then(|d| d.get("nested"))
        .and_then(|n| n.get("cross_vendor_present"))
    {
        None => false,
        Some(v) => v
            .as_bool()
            .ok_or("desktop.nested.cross_vendor_present must be a boolean")?,
    };
    let found = match (found, profile_gpu(gpus, root, data, profile)) {
        (Ok(g), Some(parent)) if auto && !cross_ok && (g.driver == "nvidia") != (parent.driver == "nvidia") => Err(format!(
            "the parent session renders on {} ({}); it cannot show buffers rendered on {} ({}) -- set desktop.nested.cross_vendor_present = true to force it",
            parent.pci, parent.driver, g.pci, g.driver
        )),
        (f, _) => f,
    };
    match found {
        Ok(g) => {
            choice.mode = RenderMode::Device;
            choice.gpu = Some(g);
        }
        Err(e) if auto => choice.degraded = Some(e),
        Err(e) => return Err(e),
    }
    Ok(choice)
}

impl RenderChoice {
    /// st_rdev of the chosen render node (what the linux-dmabuf main device is rewritten to).
    pub fn rdev(&self) -> Option<u64> {
        use std::os::unix::fs::MetadataExt;
        fs::metadata(self.gpu.as_ref()?.render.as_ref()?)
            .ok()
            .map(|m| m.rdev())
    }
    /// Environment for the nested compositor and its clients (set, then removed keys).
    pub fn env(&self) -> (Vec<(&'static str, String)>, Vec<&'static str>) {
        const NV: [&str; 3] = [
            "__NV_PRIME_RENDER_OFFLOAD",
            "__GLX_VENDOR_LIBRARY_NAME",
            "__VK_LAYER_NV_optimus",
        ];
        match self.mode {
            RenderMode::Inherit => (vec![], vec![]),
            RenderMode::Device => {
                let g = self.gpu.as_ref().expect("device mode has a GPU");
                let mut set = vec![("DRI_PRIME", g.dri_prime())];
                // glvnd loads every EGL vendor, and NVIDIA's opens the dGPU's render node while it enumerates devices.
                // A Mesa-driven node gets Mesa's vendor only, so the session never opens another GPU's node.
                if g.driver != "nvidia" {
                    set.push(("__EGL_VENDOR_LIBRARY_FILENAMES", MESA_EGL_VENDOR.into()));
                }
                (
                    set,
                    [
                        NV.as_slice(),
                        &[
                            "LIBGL_ALWAYS_SOFTWARE",
                            "GBM_ALWAYS_SOFTWARE",
                            "MESA_LOADER_DRIVER_OVERRIDE",
                        ],
                    ]
                    .concat(),
                )
            }
            RenderMode::Software => (
                vec![
                    ("LIBGL_ALWAYS_SOFTWARE", "1".into()),
                    ("GBM_ALWAYS_SOFTWARE", "1".into()),
                    ("__EGL_VENDOR_LIBRARY_FILENAMES", MESA_EGL_VENDOR.into()),
                ],
                [NV.as_slice(), &["DRI_PRIME", "MESA_LOADER_DRIVER_OVERRIDE"]].concat(),
            ),
        }
    }
    pub fn to_json(&self) -> Json {
        json!({
            "policy": self.policy, "profile": self.profile, "rule": self.rule,
            "mode": match self.mode { RenderMode::Inherit => "inherit", RenderMode::Device => "device", RenderMode::Software => "software" },
            "node": self.gpu.as_ref().and_then(|g| g.render.clone()),
            "pci": self.gpu.as_ref().map(|g| g.pci.clone()),
            "driver": self.gpu.as_ref().map(|g| g.driver.clone()),
            "degraded": self.degraded,
        })
    }
}

// ---------------------------------------------------------------------------------------------------------------
// Boot display report: read-only record of how this boot's consoles met its framebuffers.

/// Kernel log messages from /dev/kmsg (record text only). Reading needs CAP_SYSLOG under dmesg_restrict; a missing or
/// unreadable log yields an empty list, never an error. Reads until the end of the ring buffer, never blocks.
pub fn kmsg(path: &Path) -> Vec<String> {
    use std::os::unix::fs::OpenOptionsExt;
    const O_NONBLOCK: i32 = 0o4000;
    let Ok(mut f) = fs::OpenOptions::new()
        .read(true)
        .custom_flags(O_NONBLOCK)
        .open(path)
    else {
        return vec![];
    };
    let regular = f.metadata().is_ok_and(|m| m.is_file());
    let mut out = Vec::new();
    if regular {
        // A saved log (tests, a copied dmesg): one record per line, with or without the kmsg "pri,seq,ts,flags;" head.
        let mut s = String::new();
        let _ = f.read_to_string(&mut s);
        return s
            .lines()
            .filter(|l| !l.starts_with(' '))
            .map(|l| record_text(l).to_string())
            .collect();
    }
    let mut buf = vec![0u8; 8192];
    loop {
        match f.read(&mut buf) {
            Ok(0) => break,
            Ok(n) => {
                let rec = String::from_utf8_lossy(&buf[..n]);
                if let Some(first) = rec.lines().next() {
                    out.push(record_text(first).to_string());
                }
            }
            Err(e) if e.kind() == std::io::ErrorKind::BrokenPipe => continue, // overwritten records: keep reading
            Err(_) => break, // WouldBlock = end of the buffer
        }
        if out.len() > 200_000 {
            break;
        }
    }
    out
}

fn record_text(line: &str) -> &str {
    match line.split_once(';') {
        Some((head, text))
            if head.split(',').count() >= 3
                && head.split(',').all(|p| {
                    !p.is_empty() && p.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-')
                }) =>
        {
            text
        }
        _ => line,
    }
}

/// `[drm] fbN: <name> frame buffer device` registrations, in log order: (fb index, fb name, driver, PCI or "").
pub fn fb_registrations(lines: &[String]) -> Vec<(u32, String, String, String)> {
    let re =
        regex::Regex::new(r"^(?:(\S+) (\S+): )?(?:\[drm\] )?fb(\d+): (\S+) frame buffer device")
            .expect("static regex");
    lines
        .iter()
        .filter_map(|l| {
            let c = re.captures(l)?;
            let pci = c
                .get(2)
                .map(|m| m.as_str())
                .filter(|p| valid_pci(p))
                .unwrap_or("")
                .to_string();
            Some((
                c[3].parse().ok()?,
                c[4].to_string(),
                c.get(1).map(|m| m.as_str().to_string()).unwrap_or_default(),
                pci,
            ))
        })
        .collect()
}

/// The fbcon= console map of a command line: one fb digit per console, repeating (fbcon=map:10 = tty1 on fb1, tty2 on fb0, ...).
pub fn fbcon_map(cmdline: &str) -> Option<String> {
    cmdline
        .split_whitespace()
        .filter_map(|a| a.strip_prefix("fbcon="))
        .flat_map(|v| v.split(','))
        .filter_map(|o| o.strip_prefix("map:"))
        .last()
        .map(str::to_string)
}

/// The JSON boot display report. `profile_pci` is the running profile's GPU (None when it is unknown).
pub fn boot_display_report(
    h: &Host,
    vtconsole: &Path,
    cmdline: &str,
    log: &[String],
    profile: Option<&str>,
    profile_pci: Option<&str>,
) -> Json {
    let gpus = inventory(h);
    let connectors: Vec<Json> = {
        let mut v: Vec<(String, Json)> = fs::read_dir(h.sys)
            .into_iter()
            .flatten()
            .flatten()
            .filter_map(|e| {
                let name = e.file_name().to_string_lossy().into_owned();
                let (card, conn) = name.split_once('-')?;
                numbered(card, "card")?;
                let p = h.sys.join(&name);
                let read = |k: &str| fs::read_to_string(p.join(k)).map(|s| s.trim().to_string()).unwrap_or_default();
                Some((name.clone(), json!({"card": card, "connector": conn, "status": read("status"), "enabled": read("enabled")})))
            })
            .collect();
        v.sort_by(|a, b| a.0.cmp(&b.0));
        v.into_iter().map(|(_, j)| j).collect()
    };
    let mut vt: Vec<Json> = fs::read_dir(vtconsole)
        .into_iter()
        .flatten()
        .flatten()
        .map(|e| {
            let read = |k: &str| fs::read_to_string(e.path().join(k)).map(|s| s.trim().to_string()).unwrap_or_default();
            json!({"id": e.file_name().to_string_lossy(), "name": read("name"), "bind": read("bind")})
        })
        .collect();
    vt.sort_by(|a, b| a["id"].as_str().cmp(&b["id"].as_str()));
    let regs = fb_registrations(log);
    // The last registration of an index wins (an fb index is reused after its driver handed the aperture over).
    let mut fbs: Vec<(u32, String, String, String)> = Vec::new();
    for r in &regs {
        fbs.retain(|f| f.0 != r.0);
        fbs.push(r.clone());
    }
    fbs.sort();
    let interesting = regex::Regex::new(r"(?i)(fbcon|frame ?buffer|simpledrm|simple-framebuffer|efifb|vgaarb|\[drm\] Initialized|Console: )").expect("static regex");
    let events: Vec<&String> = log
        .iter()
        .filter(|l| interesting.is_match(l))
        .take(200)
        .collect();
    let map = fbcon_map(cmdline);
    let tty1_fb = map
        .as_deref()
        .and_then(|m| m.chars().next())
        .and_then(|c| c.to_digit(10));
    let profile_fb = profile_pci
        .and_then(|p| fbs.iter().find(|f| f.3 == p))
        .map(|f| f.0);
    let primary_fb = gpus
        .iter()
        .find(|g| g.boot_vga)
        .and_then(|g| fbs.iter().find(|f| f.3 == g.pci))
        .map(|f| f.0);
    let profile_is_boot_vga = profile_pci
        .and_then(|p| gpus.iter().find(|g| g.pci == p))
        .map(|g| g.boot_vga);
    let tty1_effective = tty1_fb
        .filter(|n| fbs.iter().any(|f| f.0 == *n))
        .or(if map.is_none() {
            primary_fb
        } else {
            fbs.first().map(|f| f.0)
        });
    let recommended = match (profile_fb, profile_is_boot_vga) {
        (Some(p), Some(false)) => {
            let other = fbs.iter().map(|f| f.0).find(|n| *n != p).unwrap_or(p);
            Some(format!("fbcon=map:{p}{other}"))
        }
        _ => None,
    };
    let video: Vec<&str> = cmdline
        .split_whitespace()
        .filter(|a| a.starts_with("video="))
        .collect();
    json!({
        "report": "mios-boot-display",
        "profile": profile,
        "profile_pci": profile_pci,
        "cmdline": {"fbcon_map": map, "video": video, "nvidia_drm_fbdev": cmdline.split_whitespace().any(|a| a == "nvidia-drm.fbdev=1")},
        "gpus": gpus.iter().map(Gpu::to_json).collect::<Vec<_>>(),
        "connectors": connectors,
        "vtconsoles": vt,
        "kernel_log_lines": log.len(),
        "framebuffers": fbs.iter().map(|f| json!({"fb": f.0, "name": f.1, "driver": f.2, "pci": f.3})).collect::<Vec<_>>(),
        "fb_registration_order": regs.iter().map(|f| json!({"fb": f.0, "name": f.1, "pci": f.3})).collect::<Vec<_>>(),
        "fb_events": events,
        "verdict": {
            "tty1_fb": tty1_effective,
            "profile_fb": profile_fb,
            "tty1_on_profile_gpu": match (tty1_effective, profile_fb) { (Some(t), Some(p)) => json!(t == p), _ => Json::Null },
            "recommended_fbcon": recommended,
            "note": if log.is_empty() { "kernel log unreadable or empty: run as root (CAP_SYSLOG) early in the boot" } else if regs.is_empty() { "no fb registrations in the kernel log (ring buffer wrapped?)" } else { "" },
        },
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::symlink;

    /// A fake /sys/class/drm + /dev/dri: card0 = nvidia 0000:01:00.0 (renderD129), card1 = amdgpu 0000:7a:00.0
    /// (renderD128, boot VGA) -- this machine's layout.
    pub(crate) fn fixture() -> tempfile::TempDir {
        let d = tempfile::tempdir().unwrap();
        let r = d.path();
        for (pci, drv, vga) in [
            ("0000:01:00.0", "nvidia", "0"),
            ("0000:7a:00.0", "amdgpu", "1"),
        ] {
            let dev = r.join("devices").join(pci);
            fs::create_dir_all(&dev).unwrap();
            fs::create_dir_all(r.join("drivers").join(drv)).unwrap();
            symlink(r.join("drivers").join(drv), dev.join("driver")).unwrap();
            fs::write(dev.join("boot_vga"), format!("{vga}\n")).unwrap();
        }
        fs::create_dir_all(r.join("sys")).unwrap();
        fs::create_dir_all(r.join("dri")).unwrap();
        for (node, pci) in [
            ("card0", "0000:01:00.0"),
            ("renderD129", "0000:01:00.0"),
            ("card1", "0000:7a:00.0"),
            ("renderD128", "0000:7a:00.0"),
        ] {
            fs::create_dir_all(r.join("sys").join(node)).unwrap();
            symlink(
                r.join("devices").join(pci),
                r.join("sys").join(node).join("device"),
            )
            .unwrap();
        }
        for (c, st, en) in [
            ("card0-DP-3", "connected", "enabled"),
            ("card1-HDMI-A-1", "connected", "enabled"),
        ] {
            fs::create_dir_all(r.join("sys").join(c)).unwrap();
            fs::write(r.join("sys").join(c).join("status"), st).unwrap();
            fs::write(r.join("sys").join(c).join("enabled"), en).unwrap();
        }
        fs::create_dir_all(r.join("etc/mios/hyprland")).unwrap();
        fs::write(r.join("etc/mios/hyprland/igpu-pci"), "0000:7a:00.0\n").unwrap();
        fs::write(r.join("etc/mios/hyprland/dgpu-pci"), "0000:01:00.0\n").unwrap();
        d
    }

    pub(crate) fn fixture_host(d: &Path) -> (PathBuf, PathBuf) {
        (d.join("dri"), d.join("sys"))
    }

    fn ssot() -> Value {
        toml::from_str(
            &fs::read_to_string(
                Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../usr/share/mios/mios.toml"),
            )
            .unwrap(),
        )
        .unwrap()
    }

    #[test]
    fn inventory_matches_render_nodes_by_pci_and_reads_the_boot_vga() {
        let f = fixture();
        let (dri, sys) = fixture_host(f.path());
        let g = inventory(&Host {
            dri: &dri,
            sys: &sys,
        });
        assert_eq!(g.len(), 2);
        assert_eq!(
            (
                g[0].card.file_name().unwrap(),
                g[0].pci.as_str(),
                g[0].driver.as_str(),
                g[0].boot_vga
            ),
            ("card0".as_ref(), "0000:01:00.0", "nvidia", false)
        );
        assert_eq!(g[0].render, Some(dri.join("renderD129")));
        assert_eq!(
            (g[1].pci.as_str(), g[1].driver.as_str(), g[1].boot_vga),
            ("0000:7a:00.0", "amdgpu", true)
        );
        assert_eq!(g[1].render, Some(dri.join("renderD128")));
        assert_eq!(g[1].dri_prime(), "pci-0000_7a_00_0");
    }

    #[test]
    fn nested_sessions_render_on_the_headless_igpu_in_dgpu_boots_and_software_in_igpu_boots() {
        let f = fixture();
        let (dri, sys) = fixture_host(f.path());
        let gpus = inventory(&Host {
            dri: &dri,
            sys: &sys,
        });
        let mut d = ssot();
        // dGPU boot, vendor default: the NVIDIA parent cannot show iGPU buffers (measured), so "auto" degrades and
        // says why -- the session stays visible on the parent's GPU.
        let c = nested_render(&d, f.path(), "dgpu", &gpus).unwrap();
        assert_eq!((c.rule.as_str(), c.mode), ("igpu", RenderMode::Inherit));
        assert!(
            c.degraded
                .as_deref()
                .unwrap()
                .contains("cross_vendor_present"),
            "{:?}",
            c.degraded
        );
        // With cross-vendor presentation allowed: the iGPU's render node (renderD128), never the dGPU's (renderD129).
        d["desktop"]["nested"]["cross_vendor_present"] = Value::Boolean(true);
        let c = nested_render(&d, f.path(), "dgpu", &gpus).unwrap();
        assert_eq!(
            (c.policy.as_str(), c.rule.as_str(), c.mode),
            ("auto", "igpu", RenderMode::Device)
        );
        assert_eq!(c.gpu.as_ref().unwrap().render, Some(dri.join("renderD128")));
        assert_ne!(c.gpu.as_ref().unwrap().render, Some(dri.join("renderD129")));
        assert_eq!(
            c.env().0,
            vec![
                ("DRI_PRIME", "pci-0000_7a_00_0".to_string()),
                (
                    "__EGL_VENDOR_LIBRARY_FILENAMES",
                    MESA_EGL_VENDOR.to_string()
                )
            ]
        );
        let j = c.to_json();
        assert_eq!(j["node"], json!(dri.join("renderD128")));
        assert_eq!(j["pci"], "0000:7a:00.0");
        assert_eq!(j["mode"], "device");
        // iGPU boot: the vendor rule (see nested_render in the igpu profile).
        let i = nested_render(&d, f.path(), "igpu", &gpus).unwrap();
        let want = d["desktop"]["hyprland"]["hardware_profiles"]["igpu"]["nested_render"]
            .as_str()
            .unwrap();
        assert_eq!(i.rule, want);
        match want {
            "software" => {
                assert_eq!(i.mode, RenderMode::Software);
                assert!(i.gpu.is_none());
                assert!(i
                    .env()
                    .0
                    .iter()
                    .any(|(k, v)| *k == "LIBGL_ALWAYS_SOFTWARE" && v == "1"));
            }
            _ => {
                assert_eq!(i.mode, RenderMode::Device);
                assert_eq!(i.gpu.as_ref().unwrap().pci, "0000:7a:00.0");
            }
        }
    }

    #[test]
    fn negative_inherit_and_missing_gpus() {
        let f = fixture();
        let (dri, sys) = fixture_host(f.path());
        let gpus = inventory(&Host {
            dri: &dri,
            sys: &sys,
        });
        let mut d = ssot();
        // The old behaviour, as an explicit override: no binds, the parent's main device (renderD129 here).
        d["desktop"]["nested"]["render_node"] = Value::String("inherit".into());
        let c = nested_render(&d, f.path(), "dgpu", &gpus).unwrap();
        assert_eq!(c.mode, RenderMode::Inherit);
        assert!(c.gpu.is_none() && c.env().0.is_empty() && c.rdev().is_none());
        // Explicit node and explicit profile.
        d["desktop"]["nested"]["render_node"] =
            Value::String(dri.join("renderD129").to_string_lossy().into());
        assert!(
            nested_render(&d, f.path(), "dgpu", &gpus).is_err(),
            "only /dev/dri/renderD<N> is a valid explicit node"
        );
        d["desktop"]["nested"]["render_node"] = Value::String("dgpu".into());
        let c = nested_render(&d, f.path(), "dgpu", &gpus).unwrap();
        assert!(
            !c.env()
                .0
                .iter()
                .any(|(k, _)| *k == "__EGL_VENDOR_LIBRARY_FILENAMES"),
            "NVIDIA keeps its own EGL vendor"
        );
        assert_eq!(c.gpu.unwrap().render, Some(dri.join("renderD129")));
        for bad in ["/dev/dri/card0", "../renderD128", "gpu0", ""] {
            d["desktop"]["nested"]["render_node"] = Value::String(bad.into());
            assert!(nested_render(&d, f.path(), "dgpu", &gpus).is_err(), "{bad}");
        }
        d["desktop"]["nested"]["render_node"] = Value::Integer(1);
        assert!(nested_render(&d, f.path(), "dgpu", &gpus).is_err());
        // An explicit iGPU override is honoured even across vendors.
        let mut x = ssot();
        x["desktop"]["nested"]["render_node"] = Value::String("igpu".into());
        assert_eq!(
            nested_render(&x, f.path(), "dgpu", &gpus)
                .unwrap()
                .gpu
                .unwrap()
                .pci,
            "0000:7a:00.0"
        );
        x["desktop"]["nested"]["cross_vendor_present"] = Value::String("yes".into());
        assert!(nested_render(&x, f.path(), "dgpu", &gpus).is_err());
        // auto with the iGPU gone (VM-bound): degraded to inherit, recorded; an explicit igpu override is an error.
        let only_dgpu: Vec<Gpu> = gpus
            .iter()
            .filter(|g| g.pci == "0000:01:00.0")
            .cloned()
            .collect();
        let d = ssot();
        let c = nested_render(&d, f.path(), "dgpu", &only_dgpu).unwrap();
        assert_eq!(c.mode, RenderMode::Inherit);
        assert!(c.degraded.as_deref().unwrap().contains("igpu"));
        let mut e = ssot();
        e["desktop"]["nested"]["render_node"] = Value::String("igpu".into());
        assert!(nested_render(&e, f.path(), "dgpu", &only_dgpu).is_err());
        // An invalid profile rule is rejected.
        let mut b = ssot();
        b["desktop"]["hyprland"]["hardware_profiles"]["dgpu"]["nested_render"] =
            Value::String("auto".into());
        assert!(nested_render(&b, f.path(), "dgpu", &gpus).is_err());
    }

    #[test]
    fn handoff_runs_the_fallback_profile_only_while_the_dgpu_is_away() {
        let f = fixture();
        let (dri, sys) = fixture_host(f.path());
        let gpus = inventory(&Host {
            dri: &dri,
            sys: &sys,
        });
        let d = ssot();
        assert_eq!(
            handoff_profile(&d, f.path(), "dgpu", &gpus),
            None,
            "dGPU bound to nvidia: no hand-off"
        );
        let away: Vec<Gpu> = gpus
            .iter()
            .filter(|g| g.driver != "nvidia")
            .cloned()
            .collect();
        assert_eq!(
            handoff_profile(&d, f.path(), "dgpu", &away).as_deref(),
            Some("igpu")
        );
        assert_eq!(
            handoff_profile(&d, f.path(), "igpu", &away),
            None,
            "the iGPU profile has no fallback"
        );
        // Nested sessions during the hand-off: the running profile is igpu, same driver family -> the iGPU node.
        let c = nested_render(&d, f.path(), "igpu", &away).unwrap();
        assert_eq!(
            (c.mode, c.gpu.unwrap().pci.as_str()),
            (RenderMode::Device, "0000:7a:00.0")
        );
        // Negative: an unknown fallback or a missing identity never triggers a hand-off.
        let mut b = ssot();
        b["desktop"]["hyprland"]["hardware_profiles"]["dgpu"]["handoff_fallback"] =
            Value::String("nope".into());
        assert_eq!(handoff_profile(&b, f.path(), "dgpu", &away), None);
        assert_eq!(
            handoff_profile(&d, Path::new("/nonexistent"), "dgpu", &away),
            None
        );
    }

    #[test]
    fn boot_report_reads_fb_order_and_recommends_the_dgpu_console_map() {
        let f = fixture();
        let (dri, sys) = fixture_host(f.path());
        let vt = f.path().join("vt");
        fs::create_dir_all(vt.join("vtcon1")).unwrap();
        fs::write(vt.join("vtcon1/name"), "(M) frame buffer device\n").unwrap();
        fs::write(vt.join("vtcon1/bind"), "1\n").unwrap();
        let log: Vec<String> = [
            "6,500,1000,-;simple-framebuffer simple-framebuffer.0: [drm] fb0: simpledrmdrmfb frame buffer device",
            "6,700,2000,-;fbcon: Deferring console take-over",
            "6,900,3000,-;amdgpu 0000:7a:00.0: [drm] fb0: amdgpudrmfb frame buffer device",
            "6,950,9000,-;nvidia 0000:01:00.0: [drm] fb1: nvidia-drmdrmfb frame buffer device",
            "6,960,9100,-;Console: switching to colour frame buffer device 240x67",
        ]
        .iter()
        .map(|l| record_text(l).to_string())
        .collect();
        assert_eq!(fbcon_map("quiet fbcon=map:10"), Some("10".into()));
        assert_eq!(fbcon_map("fbcon=nodefer,map:01 x"), Some("01".into()));
        let r = boot_display_report(
            &Host {
                dri: &dri,
                sys: &sys,
            },
            &vt,
            "quiet mios.desktop.gpu=dgpu fbcon=map:10",
            &log,
            Some("dgpu"),
            Some("0000:01:00.0"),
        );
        assert_eq!(
            r["framebuffers"],
            json!([{"fb":0,"name":"amdgpudrmfb","driver":"amdgpu","pci":"0000:7a:00.0"},{"fb":1,"name":"nvidia-drmdrmfb","driver":"nvidia","pci":"0000:01:00.0"}])
        );
        assert_eq!(r["verdict"]["tty1_fb"], 1);
        assert_eq!(r["verdict"]["profile_fb"], 1);
        assert_eq!(r["verdict"]["tty1_on_profile_gpu"], true);
        assert_eq!(r["verdict"]["recommended_fbcon"], "fbcon=map:10");
        assert_eq!(r["vtconsoles"][0]["name"], "(M) frame buffer device");
        assert_eq!(r["connectors"].as_array().unwrap().len(), 2);
        // Negative control: today's command line (no fbcon=) leaves tty1 on the boot-VGA iGPU.
        let r = boot_display_report(
            &Host {
                dri: &dri,
                sys: &sys,
            },
            &vt,
            "quiet mios.desktop.gpu=dgpu",
            &log,
            Some("dgpu"),
            Some("0000:01:00.0"),
        );
        assert_eq!(r["verdict"]["tty1_fb"], 0);
        assert_eq!(r["verdict"]["tty1_on_profile_gpu"], false);
        // The map names an fb that never registered (nvidia fbdev off): consoles stay on the first fb.
        let only_amd: Vec<String> = log
            .iter()
            .filter(|l| !l.contains("nvidia"))
            .cloned()
            .collect();
        let r = boot_display_report(
            &Host {
                dri: &dri,
                sys: &sys,
            },
            &vt,
            "fbcon=map:10",
            &only_amd,
            Some("dgpu"),
            Some("0000:01:00.0"),
        );
        assert_eq!(r["verdict"]["tty1_fb"], 0);
        assert_eq!(r["verdict"]["tty1_on_profile_gpu"], Json::Null);
        // Empty log: a note, no verdict.
        let r = boot_display_report(
            &Host {
                dri: &dri,
                sys: &sys,
            },
            &vt,
            "",
            &[],
            None,
            None,
        );
        assert!(r["verdict"]["note"]
            .as_str()
            .unwrap()
            .contains("unreadable"));
    }

    #[test]
    fn kmsg_reader_accepts_saved_logs_and_missing_files() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("kmsg");
        fs::write(&p, "6,1,2,-;amdgpu 0000:7a:00.0: [drm] fb0: amdgpudrmfb frame buffer device\n SUBSYSTEM=drm\nplain dmesg line\n").unwrap();
        assert_eq!(
            kmsg(&p),
            vec![
                "amdgpu 0000:7a:00.0: [drm] fb0: amdgpudrmfb frame buffer device".to_string(),
                "plain dmesg line".to_string()
            ]
        );
        assert!(kmsg(&d.path().join("missing")).is_empty());
    }
}
