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
        .next_back()
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

/// Explicit serving policy; discovery cannot change the selected backend.
#[derive(Debug)]
struct LightSettings {
    backend: String,
    image: String,
    device: String,
    layers: String,
    threads: i32,
    batch_size: i32,
    ubatch_size: i32,
    profile: String,
    drivers: Vec<String>,
    dropin: PathBuf,
    cuda_information: PathBuf,
    lock: PathBuf,
    cache: PathBuf,
}

fn light_string<'a>(table: &'a Value, key: &str) -> Result<&'a str, String> {
    table
        .get(key)
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty() && !s.chars().any(char::is_control))
        .ok_or_else(|| {
            format!("light backend {key} must be a nonempty string without control characters")
        })
}

// The server parses both positive uint32 batch counts through its native int
// argument handler, so reject values above i32::MAX instead of wrapping them.
fn light_batch_size(llama: &Value, key: &str) -> Result<i32, String> {
    llama
        .get(key)
        .and_then(Value::as_integer)
        .and_then(|n| i32::try_from(n).ok())
        .filter(|n| *n > 0)
        .ok_or_else(|| format!("llamacpp.{key} must be a positive 32-bit integer"))
}

fn light_settings(data: &Value) -> Result<LightSettings, String> {
    let llama = data.get("llamacpp").ok_or("missing [llamacpp]")?;
    let threads = llama
        .get("light_threads")
        .and_then(Value::as_integer)
        .and_then(|n| i32::try_from(n).ok())
        .filter(|n| *n > 0)
        .ok_or("llamacpp.light_threads must be a positive 32-bit integer")?;
    let batch_size = light_batch_size(llama, "light_batch_size")?;
    let ubatch_size = light_batch_size(llama, "light_ubatch_size")?;
    if ubatch_size > batch_size {
        return Err("llamacpp.light_ubatch_size must not exceed light_batch_size".into());
    }
    let slot = light_string(llama, "slot_dir")?;
    let slot = Path::new(slot);
    if !slot.is_absolute()
        || slot == Path::new("/")
        || slot
            .components()
            .any(|c| matches!(c, std::path::Component::ParentDir))
        || !slot
            .to_string_lossy()
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || b"/._-".contains(&c))
    {
        return Err("llamacpp.slot_dir must be a single absolute mounted directory".into());
    }
    let cache = slot.join("cache");
    let backend = light_string(llama, "light_backend")?;
    if !matches!(backend, "cpu" | "cuda" | "vulkan") {
        return Err(format!(
            "unknown light backend {backend:?}; expected cpu, cuda or vulkan"
        ));
    }
    let settings = llama
        .get("light_backends")
        .and_then(|b| b.get(backend))
        .ok_or_else(|| format!("missing [llamacpp.light_backends.{backend}]"))?;
    let key = light_string(settings, "image_key")?;
    if data
        .get("image")
        .is_some_and(|image| image.get(key).is_some())
    {
        return Err(format!("misplaced image.{key}; move the explicit value to canonical image.sidecars.{key} before applying"));
    }
    let image = data
        .get("image")
        .and_then(|image| image.get("sidecars"))
        .and_then(|images| images.get(key))
        .and_then(Value::as_str)
        .ok_or_else(|| format!("missing image.sidecars.{key} for light backend {backend}"))?;
    if image.is_empty()
        || image.starts_with('-')
        || !image
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || b"/._:@-".contains(&c))
    {
        return Err(format!(
            "image.sidecars.{key} is not a single OCI image reference"
        ));
    }
    let device = light_string(settings, "device")?;
    if !device
        .bytes()
        .all(|c| c.is_ascii_alphanumeric() || c == b'_')
    {
        return Err("light backend device must be a single device identifier".into());
    }
    let prefix = match backend {
        "cuda" => Some("CUDA"),
        "vulkan" => Some("Vulkan"),
        _ => None,
    };
    if prefix.is_some_and(|prefix| {
        !device
            .strip_prefix(prefix)
            .is_some_and(|suffix| !suffix.is_empty() && suffix.bytes().all(|c| c.is_ascii_digit()))
    }) {
        return Err(
            "GPU backend device must name its actual CUDA/Vulkan device, not none/software".into(),
        );
    }
    let layers = light_string(settings, "gpu_layers")?;
    if (backend == "cpu" && (layers != "0" || device != "none"))
        || (backend != "cpu" && layers != "all" && !layers.parse::<u32>().is_ok_and(|n| n > 0))
    {
        return Err(
            "CPU requires device=none/gpu_layers=0; GPU layers must be all or a positive integer"
                .into(),
        );
    }
    let drivers = settings
        .get("drivers")
        .and_then(Value::as_array)
        .ok_or("light backend drivers must be a string array")?
        .iter()
        .map(|value| {
            value
                .as_str()
                .filter(|s| {
                    !s.is_empty() && s.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'_')
                })
                .map(str::to_owned)
                .ok_or_else(|| "invalid light backend driver name".to_string())
        })
        .collect::<Result<Vec<_>, _>>()?;
    if backend != "cpu" && drivers.is_empty() {
        return Err("GPU light backend must declare eligible bound drivers".into());
    }
    let runtime = llama
        .get("light_runtime")
        .ok_or("missing [llamacpp.light_runtime]")?;
    let dropin = PathBuf::from(light_string(runtime, "dropin")?);
    if dropin.parent()
        != Some(Path::new(
            "/etc/containers/systemd/mios-llm-light.container.d",
        ))
        || !dropin.file_name().is_some_and(|name| {
            let name = name.to_string_lossy();
            name.starts_with("30-")
                && name.ends_with(".conf")
                && !name.chars().any(char::is_whitespace)
        })
    {
        return Err(
            "light dropin must be a 30-*.conf in the light container's /etc dropin directory"
                .into(),
        );
    }
    let cuda_information = PathBuf::from(light_string(runtime, "cuda_information_dir")?);
    if !cuda_information.is_absolute()
        || cuda_information
            .components()
            .any(|c| matches!(c, std::path::Component::ParentDir))
    {
        return Err("CUDA information directory must be an absolute rooted path".into());
    }
    let lock = PathBuf::from(light_string(runtime, "lock")?);
    if lock.parent() != Some(Path::new("/run/mios/light-backend"))
        || lock.file_name() != Some(std::ffi::OsStr::new("lock"))
    {
        return Err("light backend lock must be /run/mios/light-backend/lock".into());
    }
    let profile = light_string(llama, "light_gpu_profile")?;
    if profile != "auto" && !profiles(data).is_some_and(|p| p.contains_key(profile)) {
        return Err(format!("unknown light GPU hardware profile {profile:?}"));
    }
    Ok(LightSettings {
        backend: backend.into(),
        image: image.into(),
        device: device.into(),
        layers: layers.into(),
        threads,
        batch_size,
        ubatch_size,
        profile: profile.into(),
        drivers,
        dropin,
        cuda_information,
        lock,
        cache,
    })
}

fn light_gpu(
    data: &Value,
    root: &Path,
    setting: &str,
    cmdline: &str,
    gpus: &[Gpu],
) -> Result<(String, Gpu), String> {
    let profile = if setting == "auto" {
        let booted = running_profile(data, None, cmdline)?;
        handoff_profile(data, root, &booted, gpus).unwrap_or(booted)
    } else {
        setting.to_owned()
    };
    if let Some(gpu) = profile_gpu(gpus, root, data, &profile) {
        return Ok((profile, gpu));
    }
    let cfg = profiles(data)
        .and_then(|p| p.get(&profile))
        .ok_or_else(|| format!("unknown hardware profile {profile:?}"))?;
    let explicit_pci = cfg
        .get("pci")
        .and_then(Value::as_str)
        .is_some_and(|s| !s.is_empty());
    let existing_pci_file = cfg
        .get("pci_file")
        .and_then(Value::as_str)
        .is_some_and(|path| {
            path.starts_with('/') && root.join(path.trim_start_matches('/')).exists()
        });
    if explicit_pci || existing_pci_file {
        return Err(format!(
            "selected light GPU profile {profile} is absent or has an invalid PCI identity"
        ));
    }
    let alias = light_string(cfg, "device")?;
    if !alias.starts_with("/dev/dri/")
        || Path::new(alias)
            .components()
            .any(|c| matches!(c, std::path::Component::ParentDir))
    {
        return Err("hardware profile device must be a rooted DRM alias".into());
    }
    let card = fs::canonicalize(root.join(alias.trim_start_matches('/')))
        .map_err(|e| format!("selected GPU alias {alias} is unavailable: {e}"))?;
    let gpu = gpus
        .iter()
        .find(|g| fs::canonicalize(&g.card).ok().as_ref() == Some(&card))
        .cloned()
        .ok_or_else(|| format!("{alias} does not identify a discovered DRM card"))?;
    Ok((profile, gpu))
}

#[derive(Debug)]
struct LightDevice {
    path: PathBuf,
    gid: u32,
}

/// Metadata collected from stat(2) and sysfs; no external facts/bypass mode is exposed.
#[derive(Clone)]
struct LightRenderIdentity {
    path: PathBuf,
    dri: PathBuf,
    character: bool,
    device_number: String,
    kernel_number: String,
    pci: String,
    driver: String,
    gid: u32,
}

fn validate_light_render(
    gpu: &Gpu,
    drivers: &[String],
    facts: LightRenderIdentity,
) -> Result<LightDevice, String> {
    if !valid_pci(&gpu.pci) || gpu.driver == "vfio-pci" || !drivers.contains(&gpu.driver) {
        return Err(format!(
            "selected GPU {} is not bound to an eligible driver ({})",
            gpu.pci, gpu.driver
        ));
    }
    let name = facts
        .path
        .file_name()
        .and_then(|s| s.to_str())
        .ok_or("invalid DRM render name")?;
    if facts.path.parent() != Some(facts.dri.as_path()) || numbered(name, "renderD").is_none() {
        return Err("render node does not resolve inside the configured DRM directory".into());
    }
    if !facts.character {
        return Err("selected render path is not a character device".into());
    }
    if facts.kernel_number.trim() != facts.device_number
        || facts.pci != gpu.pci
        || facts.driver != gpu.driver
    {
        return Err(
            "DRM node metadata does not match the selected kernel PCI device/driver".into(),
        );
    }
    Ok(LightDevice {
        path: facts.path,
        gid: facts.gid,
    })
}

/// Stat the resolved node and compare it to the selected kernel PCI/driver.
#[cfg(unix)]
fn light_render_device(gpu: &Gpu, host: &Host, drivers: &[String]) -> Result<LightDevice, String> {
    use std::os::unix::fs::{FileTypeExt, MetadataExt};
    let render = gpu
        .render
        .as_ref()
        .ok_or("selected GPU has no render node")?;
    let path = fs::canonicalize(render).map_err(|e| format!("render node is unavailable: {e}"))?;
    let dri =
        fs::canonicalize(host.dri).map_err(|e| format!("DRM directory is unavailable: {e}"))?;
    let name = path
        .file_name()
        .and_then(|s| s.to_str())
        .ok_or("invalid DRM render name")?;
    let metadata =
        fs::metadata(&path).map_err(|e| format!("render metadata is unreadable: {e}"))?;
    let device = host.sys.join(name).join("device");
    let facts = LightRenderIdentity {
        path: path.clone(),
        dri,
        character: metadata.file_type().is_char_device(),
        device_number: format!(
            "{}:{}",
            rustix::fs::major(metadata.rdev()),
            rustix::fs::minor(metadata.rdev())
        ),
        kernel_number: fs::read_to_string(host.sys.join(name).join("dev"))
            .map_err(|e| format!("DRM kernel device identity is unreadable: {e}"))?,
        pci: link_name(&device),
        driver: link_name(&device.join("driver")),
        gid: metadata.gid(),
    };
    validate_light_render(gpu, drivers, facts)
}

#[cfg(not(unix))]
fn light_render_device(
    _gpu: &Gpu,
    _host: &Host,
    _drivers: &[String],
) -> Result<LightDevice, String> {
    Err("light GPU projection requires Linux device metadata".into())
}

#[cfg(unix)]
fn light_cuda_device(
    root: &Path,
    data: &Value,
    cfg: &LightSettings,
    gpu: &Gpu,
) -> Result<(String, u32), String> {
    use std::os::unix::fs::{FileTypeExt, MetadataExt};
    if gpu.driver != "nvidia" {
        return Err("CUDA requires the selected GPU to be bound to the NVIDIA driver".into());
    }
    let info = root
        .join(
            cfg.cuda_information
                .strip_prefix("/")
                .map_err(|e| e.to_string())?,
        )
        .join(&gpu.pci)
        .join("information");
    let info = fs::read_to_string(info)
        .map_err(|e| format!("selected NVIDIA information is unavailable: {e}"))?;
    let field = |key: &str| {
        info.lines().find_map(|line| {
            line.split_once(':')
                .filter(|(name, _)| name.trim() == key)
                .map(|(_, value)| value.trim())
        })
    };
    let minor = field("Device Minor")
        .and_then(|s| s.parse::<u32>().ok())
        .ok_or("selected NVIDIA Device Minor is missing or invalid")?;
    let metadata = fs::metadata(root.join(format!("dev/nvidia{minor}")))
        .map_err(|e| format!("selected NVIDIA device node is unavailable: {e}"))?;
    if !metadata.file_type().is_char_device() {
        return Err("selected NVIDIA device is not a character device".into());
    }
    let device = root.join(format!(
        "sys/dev/char/{}:{}/device",
        rustix::fs::major(metadata.rdev()),
        rustix::fs::minor(metadata.rdev())
    ));
    if link_name(&device) != gpu.pci {
        return Err("NVIDIA device node does not match the selected GPU PCI identity".into());
    }
    let selection = data
        .get("gpu")
        .and_then(|g| g.get("cdi"))
        .and_then(|c| c.get("nvidia"))
        .and_then(|c| c.get("device"))
        .and_then(Value::as_str)
        .ok_or("gpu.cdi.nvidia.device is missing")?;
    let (kind, requested) = selection
        .split_once('=')
        .ok_or("invalid configured NVIDIA CDI selection")?;
    if kind.is_empty()
        || !kind
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || b"._/-".contains(&c))
        || !(requested == "all"
            || requested == minor.to_string()
            || field("GPU UUID") == Some(requested))
    {
        return Err("configured NVIDIA CDI device does not match the selected GPU".into());
    }
    // Source-wide '=all' narrows to the selected card; VFIO cards never pass validation.
    Ok((format!("{kind}={minor}"), metadata.gid()))
}

#[cfg(not(unix))]
fn light_cuda_device(
    _root: &Path,
    _data: &Value,
    _cfg: &LightSettings,
    _gpu: &Gpu,
) -> Result<(String, u32), String> {
    Err("CUDA projection requires Linux device metadata".into())
}

#[derive(Debug, Clone)]
pub struct LightBackendPlan {
    pub backend: String,
    pub image: String,
    pub profile: Option<String>,
    pub pci: Option<String>,
    pub output: PathBuf,
    pub contents: String,
}

impl LightBackendPlan {
    pub fn to_json(&self, changed: bool) -> Json {
        json!({"backend":self.backend,"image":self.image,"profile":self.profile,"pci":self.pci,
            "output":self.output,"contents":self.contents,"changed":changed})
    }
}

pub fn light_backend_plan(
    root: &Path,
    data: &Value,
    host: &Host,
    cmdline: &str,
) -> Result<LightBackendPlan, String> {
    let cfg = light_settings(data)?;
    let mut devices = Vec::new();
    let mut groups = Vec::new();
    let mut profile = None;
    let mut pci = None;
    if cfg.backend != "cpu" {
        let gpus = inventory(host);
        let (selected, gpu) = light_gpu(data, root, &cfg.profile, cmdline, &gpus)?;
        let render = light_render_device(&gpu, host, &cfg.drivers)?;
        if cfg.backend == "vulkan" {
            let path = render
                .path
                .strip_prefix(root)
                .map_err(|_| "render node escapes the selected root")?;
            devices.push(format!("/{}", path.display()));
            groups.push(render.gid);
        } else {
            let (device, gid) = light_cuda_device(root, data, &cfg, &gpu)?;
            devices.push(device);
            groups.extend([render.gid, gid]);
        }
        profile = Some(selected);
        pci = Some(gpu.pci);
    }
    groups.sort();
    groups.dedup();
    let mut contents = format!("# AI-hint: Native light backend projection from layered mios.toml and verified kernel device metadata.\n[Container]\nImage={}\nAddDevice=\nGroupAdd=\n",cfg.image);
    for device in devices {
        contents.push_str(&format!("AddDevice={device}\n"));
    }
    for group in groups {
        contents.push_str(&format!("GroupAdd={group}\n"));
    }
    contents.push_str(&format!(
        "Environment=LLAMA_ARG_DEVICE={}\nEnvironment=LLAMA_ARG_N_GPU_LAYERS={}\nEnvironment=LLAMA_ARG_THREADS={}\nEnvironment=LLAMA_ARG_BATCH={}\nEnvironment=LLAMA_ARG_UBATCH={}\n",
        cfg.device, cfg.layers, cfg.threads, cfg.batch_size, cfg.ubatch_size
    ));
    contents.push_str(&format!(
        "Environment=XDG_CACHE_HOME={}\n",
        cfg.cache.display()
    ));
    Ok(LightBackendPlan {
        backend: cfg.backend,
        image: cfg.image,
        profile,
        pci,
        output: root.join(cfg.dropin.strip_prefix("/").map_err(|e| e.to_string())?),
        contents,
    })
}

/// Output parents and targets cannot be redirected by host symlinks.
fn light_output_parents(root: &Path, output: &Path) -> Result<(), String> {
    let relative = output
        .strip_prefix(root)
        .map_err(|_| "light output escapes the selected root")?;
    let mut current = root.to_path_buf();
    for component in relative.components() {
        if !matches!(component, std::path::Component::Normal(_)) {
            return Err("light output contains an invalid path component".into());
        }
        current.push(component);
        match fs::symlink_metadata(&current) {
            Ok(metadata) if metadata.file_type().is_symlink() => {
                return Err(format!(
                    "light output symlink is forbidden: {}",
                    current.display()
                ))
            }
            Ok(_) => {}
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => return Err(format!("light output is unreadable: {error}")),
        }
    }
    Ok(())
}

pub fn apply_light_backend(root: &Path, plan: &LightBackendPlan) -> Result<bool, String> {
    use std::io::Write;
    light_output_parents(root, &plan.output)?;
    let existing = read_light_output(&plan.output)?;
    if let Some(contents) = existing.as_ref() {
        if contents.lines().next() != plan.contents.lines().next() {
            return Err("existing light dropin lacks the native ownership header; preserve it and reconcile explicitly".into());
        }
    }
    if existing.as_deref() == Some(&plan.contents) {
        return Ok(false);
    }
    let parent = plan.output.parent().ok_or("light output has no parent")?;
    fs::create_dir_all(parent).map_err(|e| format!("cannot create light dropin directory: {e}"))?;
    light_output_parents(root, &plan.output)?;
    let mut temporary = tempfile::NamedTempFile::new_in(parent).map_err(|e| e.to_string())?;
    temporary
        .write_all(plan.contents.as_bytes())
        .map_err(|e| e.to_string())?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        temporary
            .as_file()
            .set_permissions(fs::Permissions::from_mode(0o644))
            .map_err(|e| e.to_string())?;
    }
    temporary.as_file().sync_all().map_err(|e| e.to_string())?;
    temporary.persist(&plan.output).map_err(|e| e.to_string())?;
    Ok(true)
}

fn read_light_output(path: &Path) -> Result<Option<String>, String> {
    match fs::read_to_string(path) {
        Ok(text) => Ok(Some(text)),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(format!("existing light output is unreadable: {error}")),
    }
}

fn lock_light_backend(root: &Path, path: &Path) -> Result<fs::File, String> {
    let path = root.join(path.strip_prefix("/").map_err(|e| e.to_string())?);
    light_output_parents(root, &path)?;
    fs::create_dir_all(path.parent().ok_or("lock has no parent")?).map_err(|e| e.to_string())?;
    let mut options = fs::OpenOptions::new();
    options.read(true).write(true).create(true).truncate(false);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options
            .mode(0o600)
            .custom_flags(rustix::fs::OFlags::NOFOLLOW.bits() as i32);
    }
    let file = options
        .open(&path)
        .map_err(|e| format!("cannot open light backend lock: {e}"))?;
    if !file.metadata().map_err(|e| e.to_string())?.is_file() {
        return Err("light backend lock is not a regular file".into());
    }
    file.try_lock()
        .map_err(|e| format!("light backend reconciliation lock unavailable: {e}"))?;
    Ok(file)
}

/// Roll back a failed reload so the next attempt must retry it; no false applied state.
fn apply_light_with_reload(
    root: &Path,
    plan: &LightBackendPlan,
    reload: &mut dyn FnMut() -> Result<(), String>,
) -> Result<bool, String> {
    light_output_parents(root, &plan.output)?;
    let previous = read_light_output(&plan.output)?;
    let changed = apply_light_backend(root, plan)?;
    if changed {
        if let Err(error) = reload() {
            if read_light_output(&plan.output)?.as_deref() == Some(&plan.contents) {
                if let Some(contents) = previous {
                    let mut prior = plan.clone();
                    prior.contents = contents;
                    apply_light_backend(root, &prior)?;
                } else {
                    fs::remove_file(&plan.output)
                        .map_err(|e| format!("cannot roll back failed light reload: {e}"))?;
                }
            }
            return Err(error);
        }
    }
    Ok(changed)
}

pub fn reconcile_light_backend(root: &Path, apply: bool, reload: bool) -> Result<Json, String> {
    if reload && (!apply || root != Path::new("/")) {
        return Err("--reload requires --apply on the live root".into());
    }
    let data = mios_resolver::resolve_merged(Some(root), false).map_err(|e| e.to_string())?;
    let settings = light_settings(&data)?;
    let _lock = if apply {
        Some(lock_light_backend(root, &settings.lock)?)
    } else {
        None
    };
    let dri = root.join("dev/dri");
    let sys = root.join("sys/class/drm");
    let cmdline = fs::read_to_string(root.join("proc/cmdline")).unwrap_or_default();
    let plan = light_backend_plan(
        root,
        &data,
        &Host {
            dri: &dri,
            sys: &sys,
        },
        &cmdline,
    )?;
    let changed = if reload {
        apply_light_with_reload(root, &plan, &mut || {
            let status = std::process::Command::new("systemctl")
                .arg("daemon-reload")
                .status()
                .map_err(|e| format!("cannot reload systemd: {e}"))?;
            if status.success() {
                Ok(())
            } else {
                Err(format!("systemd daemon-reload failed: {status}"))
            }
        })?
    } else {
        apply && apply_light_backend(root, &plan)?
    };
    Ok(plan.to_json(changed))
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
    fn light_cpu_plan_is_device_free_and_apply_is_idempotent() {
        let root = tempfile::tempdir().unwrap();
        let data = ssot();
        let dri = root.path().join("absent-dri");
        let sys = root.path().join("absent-sys");
        let plan = light_backend_plan(
            root.path(),
            &data,
            &Host {
                dri: &dri,
                sys: &sys,
            },
            "",
        )
        .unwrap();
        assert_eq!(plan.backend, "cpu");
        assert!(plan.image.ends_with(":cpu"));
        assert!(plan.contents.contains("AddDevice=\nGroupAdd=\n"));
        assert!(!plan.contents.contains("AddDevice=/dev/"));
        assert!(plan.contents.contains("LLAMA_ARG_DEVICE=none"));
        assert!(!root.path().join("etc").exists(), "planning wrote files");
        assert!(apply_light_backend(root.path(), &plan).unwrap());
        let before = fs::metadata(&plan.output).unwrap().modified().unwrap();
        assert!(!apply_light_backend(root.path(), &plan).unwrap());
        assert_eq!(
            before,
            fs::metadata(&plan.output).unwrap().modified().unwrap()
        );
    }

    #[test]
    fn light_thread_budget_changes_the_runtime_projection_and_reload() {
        let root = tempfile::tempdir().unwrap();
        let mut data = ssot();
        data["llamacpp"]["light_backend"] = Value::String("cpu".into());
        data["llamacpp"]["light_threads"] = Value::Integer(1);
        let absent = root.path().join("absent");
        let host = Host {
            dri: &absent,
            sys: &absent,
        };
        let first = light_backend_plan(root.path(), &data, &host, "").unwrap();
        assert!(first.contents.contains("Environment=LLAMA_ARG_THREADS=1\n"));
        assert_eq!(
            first
                .contents
                .lines()
                .filter(|line| line.starts_with("Environment=LLAMA_ARG_THREADS="))
                .count(),
            1
        );
        assert!(!first.contents.contains("LLAMA_ARG_THREADS_BATCH"));
        let mut reloads = 0;
        assert!(apply_light_with_reload(root.path(), &first, &mut || {
            reloads += 1;
            Ok(())
        })
        .unwrap());
        assert!(!apply_light_with_reload(root.path(), &first, &mut || {
            reloads += 1;
            Ok(())
        })
        .unwrap());
        assert_eq!(reloads, 1);

        // Change only the existing operator knob, leaving the backend, image,
        // device, cache and other policy untouched.
        data["llamacpp"]["light_threads"] = Value::Integer(4);
        let changed = light_backend_plan(root.path(), &data, &host, "").unwrap();
        assert_eq!(changed.output, first.output);
        assert_eq!(
            changed.contents,
            first
                .contents
                .replace("LLAMA_ARG_THREADS=1\n", "LLAMA_ARG_THREADS=4\n")
        );
        assert!(apply_light_with_reload(root.path(), &changed, &mut || {
            reloads += 1;
            Ok(())
        })
        .unwrap());
        assert_eq!(reloads, 2);
        assert_eq!(
            fs::read_to_string(&changed.output).unwrap(),
            changed.contents
        );
        assert!(!apply_light_with_reload(root.path(), &changed, &mut || {
            reloads += 1;
            Ok(())
        })
        .unwrap());
        assert_eq!(reloads, 2, "unchanged thread budget must not reload");
    }

    #[test]
    fn light_thread_budget_rejects_invalid_values_before_touching_managed_output() {
        let root = tempfile::tempdir().unwrap();
        let mut data = ssot();
        data["llamacpp"]["light_backend"] = Value::String("cpu".into());
        let absent = root.path().join("absent");
        let host = Host {
            dri: &absent,
            sys: &absent,
        };
        let first = light_backend_plan(root.path(), &data, &host, "").unwrap();
        apply_light_backend(root.path(), &first).unwrap();
        let before = fs::read_to_string(&first.output).unwrap();
        for value in [
            Value::Integer(0),
            Value::Integer(-1),
            Value::Integer(i64::from(i32::MAX) + 1),
            Value::Float(1.5),
            Value::String("4".into()),
            Value::Boolean(true),
        ] {
            let mut invalid = data.clone();
            invalid["llamacpp"]["light_threads"] = value;
            assert!(light_backend_plan(root.path(), &invalid, &host, "")
                .unwrap_err()
                .contains("llamacpp.light_threads"));
            assert_eq!(fs::read_to_string(&first.output).unwrap(), before);
        }
        data["llamacpp"]
            .as_table_mut()
            .unwrap()
            .remove("light_threads");
        assert!(light_backend_plan(root.path(), &data, &host, "")
            .unwrap_err()
            .contains("llamacpp.light_threads"));
        assert_eq!(fs::read_to_string(&first.output).unwrap(), before);
    }

    #[test]
    fn light_batch_budget_changes_only_its_environment_and_reloads_once() {
        let root = tempfile::tempdir().unwrap();
        let mut data = ssot();
        data["llamacpp"]["light_backend"] = Value::String("cpu".into());
        let absent = root.path().join("absent");
        let host = Host {
            dri: &absent,
            sys: &absent,
        };
        let first = light_backend_plan(root.path(), &data, &host, "").unwrap();
        assert!(first
            .contents
            .contains("Environment=LLAMA_ARG_BATCH=2048\n"));
        assert!(first
            .contents
            .contains("Environment=LLAMA_ARG_UBATCH=512\n"));
        apply_light_backend(root.path(), &first).unwrap();

        data["llamacpp"]["light_batch_size"] = Value::Integer(128);
        data["llamacpp"]["light_ubatch_size"] = Value::Integer(64);
        let changed = light_backend_plan(root.path(), &data, &host, "").unwrap();
        assert_eq!(changed.output, first.output);
        assert_eq!(
            changed.contents,
            first
                .contents
                .replace("LLAMA_ARG_BATCH=2048\n", "LLAMA_ARG_BATCH=128\n")
                .replace("LLAMA_ARG_UBATCH=512\n", "LLAMA_ARG_UBATCH=64\n")
        );
        let mut reloads = 0;
        assert!(apply_light_with_reload(root.path(), &changed, &mut || {
            reloads += 1;
            Ok(())
        })
        .unwrap());
        let modified = fs::metadata(&changed.output).unwrap().modified().unwrap();
        assert!(!apply_light_with_reload(root.path(), &changed, &mut || {
            reloads += 1;
            Ok(())
        })
        .unwrap());
        assert_eq!(reloads, 1);
        assert_eq!(
            fs::metadata(&changed.output).unwrap().modified().unwrap(),
            modified
        );
        assert_eq!(
            fs::read_to_string(&changed.output).unwrap(),
            changed.contents
        );

        // Equal physical/logical budgets and the server parser's bounds are valid.
        for bound in [1, i64::from(i32::MAX)] {
            data["llamacpp"]["light_batch_size"] = Value::Integer(bound);
            data["llamacpp"]["light_ubatch_size"] = Value::Integer(bound);
            assert!(light_backend_plan(root.path(), &data, &host, "").is_ok());
        }
    }

    #[test]
    fn invalid_light_batch_budgets_preserve_existing_managed_output() {
        let root = tempfile::tempdir().unwrap();
        let mut data = ssot();
        data["llamacpp"]["light_backend"] = Value::String("cpu".into());
        let absent = root.path().join("absent");
        let host = Host {
            dri: &absent,
            sys: &absent,
        };
        let first = light_backend_plan(root.path(), &data, &host, "").unwrap();
        apply_light_backend(root.path(), &first).unwrap();
        let before = fs::read(&first.output).unwrap();
        let modified = fs::metadata(&first.output).unwrap().modified().unwrap();
        for key in ["light_batch_size", "light_ubatch_size"] {
            for value in [
                Value::Integer(0),
                Value::Integer(-1),
                Value::Integer(i64::from(i32::MAX) + 1),
                Value::Integer(i64::from(u32::MAX) + 1),
                Value::Float(64.5),
                Value::String("64".into()),
                Value::Boolean(true),
            ] {
                let mut invalid = data.clone();
                invalid["llamacpp"][key] = value;
                assert!(light_backend_plan(root.path(), &invalid, &host, "")
                    .unwrap_err()
                    .contains(key));
                assert_eq!(fs::read(&first.output).unwrap(), before);
                assert_eq!(
                    fs::metadata(&first.output).unwrap().modified().unwrap(),
                    modified
                );
            }
            let mut missing = data.clone();
            missing["llamacpp"].as_table_mut().unwrap().remove(key);
            assert!(light_backend_plan(root.path(), &missing, &host, "")
                .unwrap_err()
                .contains(key));
        }
        data["llamacpp"]["light_ubatch_size"] = Value::Integer(2049);
        assert!(light_backend_plan(root.path(), &data, &host, "")
            .unwrap_err()
            .contains("must not exceed"));
        assert_eq!(fs::read(&first.output).unwrap(), before);
        assert_eq!(
            fs::metadata(&first.output).unwrap().modified().unwrap(),
            modified
        );
    }

    #[test]
    fn light_render_identity_binds_one_real_node_and_derives_its_group() {
        let gpu = Gpu {
            card: "/dev/dri/card9".into(),
            render: Some("/dev/dri/renderD211".into()),
            pci: "0000:09:00.0".into(),
            driver: "amdgpu".into(),
            boot_vga: false,
        };
        let facts = LightRenderIdentity {
            path: "/dev/dri/renderD211".into(),
            dri: "/dev/dri".into(),
            character: true,
            device_number: "226:211".into(),
            kernel_number: "226:211\n".into(),
            pci: gpu.pci.clone(),
            driver: gpu.driver.clone(),
            gid: 581,
        };
        let drivers = vec!["amdgpu".into()];
        let validated = validate_light_render(&gpu, &drivers, facts.clone()).unwrap();
        assert_eq!(validated.path, Path::new("/dev/dri/renderD211"));
        assert_eq!(validated.gid, 581, "do not hardcode a host render gid");
        for fault in 0..6 {
            let mut bad = facts.clone();
            match fault {
                0 => bad.character = false,
                1 => bad.kernel_number = "226:128".into(),
                2 => bad.pci = "0000:01:00.0".into(),
                3 => bad.driver = "nvidia".into(),
                4 => bad.path = "/tmp/renderD211".into(),
                _ => bad.path = "/dev/dri/card9".into(),
            }
            assert!(
                validate_light_render(&gpu, &drivers, bad).is_err(),
                "accepted fault{fault}"
            );
        }
        let mut vfio = gpu.clone();
        vfio.driver = "vfio-pci".into();
        assert!(validate_light_render(&vfio, &["vfio-pci".into()], facts.clone()).is_err());
        assert!(validate_light_render(&gpu, &["nvidia".into()], facts).is_err());
    }

    #[test]
    fn light_backend_rejects_unknown_policies_and_output_injection() {
        let source = ssot();
        for fault in 0..7 {
            let mut data = source.clone();
            match fault {
                0 => data["llamacpp"]["light_backend"] = Value::String("typo".into()),
                1 => {
                    data["llamacpp"]["light_gpu_profile"] = Value::String("missing-profile".into())
                }
                2 => {
                    data["llamacpp"]["light_backends"]["cpu"]["device"] =
                        Value::String("none\nAddDevice=/dev/kfd".into())
                }
                3 => {
                    data["image"]["sidecars"]["llm_light_cpu"] =
                        Value::String("image\n[Service]\nExecStart=unsafe".into())
                }
                4 => {
                    data["llamacpp"]["light_runtime"]["dropin"] =
                        Value::String("/etc/other.conf".into())
                }
                5 => {
                    data["llamacpp"]["light_runtime"]["lock"] =
                        Value::String("/tmp/arbitrary".into())
                }
                _ => {
                    data["llamacpp"]["light_backends"]["cpu"]["gpu_layers"] =
                        Value::String("all".into())
                }
            }
            assert!(light_settings(&data).is_err(), "accepted fault{fault}");
        }
    }

    #[test]
    fn light_rejects_a_misplaced_image_pin_instead_of_replacing_it_with_a_default() {
        let mut data = ssot();
        data["image"].as_table_mut().unwrap().insert(
            "llm_light_cpu".into(),
            Value::String("registry.test/explicit@sha256:deadbeef".into()),
        );
        let error = light_settings(&data).unwrap_err();
        assert!(
            error.contains("canonical image.sidecars.llm_light_cpu"),
            "{error}"
        );
    }

    #[test]
    fn light_profile_never_substitutes_an_available_other_gpu() {
        let fixture = fixture();
        let (dri, sys) = fixture_host(fixture.path());
        let gpus = inventory(&Host {
            dri: &dri,
            sys: &sys,
        });
        let data = ssot();
        let (profile, gpu) = light_gpu(
            &data,
            fixture.path(),
            "auto",
            "mios.desktop.gpu=igpu",
            &gpus,
        )
        .unwrap();
        assert_eq!(profile, "igpu");
        assert_eq!(gpu.driver, "amdgpu");
        let other: Vec<_> = gpus.into_iter().filter(|g| g.driver == "nvidia").collect();
        assert!(light_gpu(&data, fixture.path(), "igpu", "", &other).is_err());
    }

    #[test]
    fn light_reload_failure_restores_prior_output_and_retries() {
        let root = tempfile::tempdir().unwrap();
        let data = ssot();
        let absent = root.path().join("absent");
        let plan = light_backend_plan(
            root.path(),
            &data,
            &Host {
                dri: &absent,
                sys: &absent,
            },
            "",
        )
        .unwrap();
        let result = apply_light_with_reload(root.path(), &plan, &mut || {
            Err("planted reload denial".into())
        });
        assert!(result.is_err());
        assert!(
            !plan.output.exists(),
            "failed reload left an applied-looking file"
        );
        let mut calls = 0;
        assert!(apply_light_with_reload(root.path(), &plan, &mut || {
            calls += 1;
            Ok(())
        })
        .unwrap());
        assert_eq!(calls, 1);
        assert!(!apply_light_with_reload(root.path(), &plan, &mut || {
            calls += 1;
            Ok(())
        })
        .unwrap());
        assert_eq!(calls, 1, "unchanged projection reloaded systemd");
        let before = fs::read_to_string(&plan.output).unwrap();
        let mut changed = plan.clone();
        changed.contents.push_str("# changed\n");
        assert!(apply_light_with_reload(root.path(), &changed, &mut || Err(
            "planted failure".into()
        ))
        .is_err());
        assert_eq!(fs::read_to_string(&plan.output).unwrap(), before);
    }

    #[test]
    fn light_shader_cache_stays_below_the_configured_private_slot_mount() {
        let root = tempfile::tempdir().unwrap();
        let mut data = ssot();
        data["llamacpp"]["slot_dir"] = Value::String("/srv/mios-private/slots".into());
        let absent = root.path().join("absent");
        let plan = light_backend_plan(
            root.path(),
            &data,
            &Host {
                dri: &absent,
                sys: &absent,
            },
            "",
        )
        .unwrap();
        assert!(plan
            .contents
            .ends_with("Environment=XDG_CACHE_HOME=/srv/mios-private/slots/cache\n"));
        assert!(!plan.contents.contains("/var/lib/mios/llamacpp/.cache"));
        data["llamacpp"]["slot_dir"] = Value::String("/".into());
        assert!(light_settings(&data).is_err());
    }

    #[test]
    fn light_refuses_unmanaged_or_unreadable_existing_output() {
        let root = tempfile::tempdir().unwrap();
        let data = ssot();
        let absent = root.path().join("absent");
        let plan = light_backend_plan(
            root.path(),
            &data,
            &Host {
                dri: &absent,
                sys: &absent,
            },
            "",
        )
        .unwrap();
        fs::create_dir_all(plan.output.parent().unwrap()).unwrap();
        fs::write(&plan.output, "# operator-owned\n[Container]\n").unwrap();
        assert!(apply_light_backend(root.path(), &plan).is_err());
        assert_eq!(
            fs::read_to_string(&plan.output).unwrap(),
            "# operator-owned\n[Container]\n"
        );
        fs::write(&plan.output, [0xff]).unwrap();
        assert!(apply_light_backend(root.path(), &plan).is_err());
        assert_eq!(fs::read(&plan.output).unwrap(), [0xff]);
    }

    #[test]
    fn light_output_and_lock_refuse_redirects_and_conflicting_writers() {
        let root = tempfile::tempdir().unwrap();
        let target = tempfile::tempdir().unwrap();
        let data = ssot();
        let absent = root.path().join("absent");
        let plan = light_backend_plan(
            root.path(),
            &data,
            &Host {
                dri: &absent,
                sys: &absent,
            },
            "",
        )
        .unwrap();
        fs::create_dir_all(root.path().join("etc/containers")).unwrap();
        symlink(target.path(), root.path().join("etc/containers/systemd")).unwrap();
        assert!(apply_light_backend(root.path(), &plan).is_err());
        assert!(fs::read_dir(target.path()).unwrap().next().is_none());
        let cfg = light_settings(&data).unwrap();
        let lock = lock_light_backend(root.path(), &cfg.lock).unwrap();
        assert!(lock_light_backend(root.path(), &cfg.lock).is_err());
        drop(lock);
        assert!(lock_light_backend(root.path(), &cfg.lock).is_ok());
    }

    #[test]
    fn light_native_service_is_required_before_start_and_revalidates() {
        let data = ssot();
        let unit = &data["units"]["mios-light-backend.service"];
        assert_eq!(unit["Service"]["Type"].as_str(), Some("oneshot"));
        assert_eq!(unit["Service"]["RemainAfterExit"].as_str(), Some("no"));
        assert!(unit["Service"]["ExecStart"]
            .as_str()
            .unwrap()
            .ends_with("light-backend --apply --reload"));
        let light = &data["containers"]["mios-llm-light"]["Unit"];
        assert_eq!(
            light["Requires"].as_str(),
            Some("mios-light-backend.service")
        );
        assert!(light["After"]
            .as_str()
            .unwrap()
            .contains("mios-light-backend.service"));
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
