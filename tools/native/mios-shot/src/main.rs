// AI-hint: mios-shot -- the MiOS native Linux screenshot tool (Hyprland first, any wlroots-protocol compositor, the
// Screenshot portal elsewhere). Modes: region (frozen frame + the glass selector in mios-shell, slurp fallback),
// window (active or by stableId; frozen crop or ext foreign-toplevel capture), output (focused or named), all. Every
// option comes from the layered SSOT [desktop.screenshot] (paths, naming, png/jpg/webp, quality, delay, cursor,
// sound/flash, notification actions, editor hand-off) with CLI overrides. A separate binary on purpose: Hyprland
// grants screencopy per executable path, so capture never rides on the multi-call miosd.
// Section index: mod img (pixels, compositing, encoding, naming, atomic save) | mod wl (Wayland capture, freeze,
// clipboard) | CLI | settings | Hyprland IPC | capture flow | selector (shell socket / slurp) | output side effects
// (save, clipboard, notification, editor, flash, sound) | portal | probe | residents (__clip, __notify-wait).
// AI-related: usr/share/mios/mios.toml
// [desktop.screenshot], usr/share/mios/quickshell/Shot.qml, usr/share/mios/quickshell/Config.qml,
// usr/libexec/mios/mios-screenshot, tools/native/mios-gen/src/render.rs (screenshot bindings projection)


use img::{Canvas, Format, Placed, Rect};
use serde_json::{json, Value as Json};
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Read, Write};
use std::os::fd::{AsRawFd, OwnedFd};
use std::os::unix::net::{UnixListener, UnixStream};
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

mod img {
    //! AI-hint: Pixel side of mios-shot: reads captured shm buffers (XRGB/ARGB/XBGR/ABGR 8888 and the 2101010 variants,
    //! down-converted to 8 bit), composites any logical rectangle across outputs at one scale (grim's rule: the largest
    //! scale of the outputs it touches, edges rounded outward; nearest sample at pixel centres, so a region inside one
    //! output is a pixel-exact crop of its buffer), applies each frame's wl_output transform and y-invert, encodes
    //! png / jpg / webp (lossless), renders [desktop.screenshot] filename templates (strftime + {mode} {output} {app}) and
    //! writes files atomically without clobbering (temp file + renameat2 NOREPLACE, "-N" suffix on collision).
    //! AI-related: usr/share/mios/mios.toml [desktop.screenshot]

    use std::io::Write;
    use std::path::{Path, PathBuf};

    /// A read-only view of one captured buffer.
    pub struct Pixels<'a> {
        pub width: u32,
        pub height: u32,
        pub stride: u32,
        pub format: u32,
        pub transform: u32,
        pub y_invert: bool,
        pub data: &'a [u8],
    }

    /// Where a buffer sits in the logical (compositor) space, and whether it is opaque screen content.
    pub struct Placed<'a> {
        pub px: Pixels<'a>,
        pub x: f64,
        pub y: f64,
        pub w: f64,
        pub h: f64,
        pub opaque: bool,
    }

    #[derive(Debug, Clone, Copy, PartialEq)]
    pub struct Rect {
        pub x: f64,
        pub y: f64,
        pub w: f64,
        pub h: f64,
    }

    impl Rect {
        pub fn intersects(&self, o: &Rect) -> bool {
            self.x < o.x + o.w && o.x < self.x + self.w && self.y < o.y + o.h && o.y < self.y + self.h
        }
        pub fn union(&self, o: &Rect) -> Rect {
            let x = self.x.min(o.x);
            let y = self.y.min(o.y);
            Rect { x, y, w: (self.x + self.w).max(o.x + o.w) - x, h: (self.y + self.h).max(o.y + o.h) - y }
        }
        pub fn intersect(&self, o: &Rect) -> Option<Rect> {
            let x = self.x.max(o.x);
            let y = self.y.max(o.y);
            let w = (self.x + self.w).min(o.x + o.w) - x;
            let h = (self.y + self.h).min(o.y + o.h) - y;
            (w > 0.0 && h > 0.0).then_some(Rect { x, y, w, h })
        }
    }

    pub struct Canvas {
        pub w: u32,
        pub h: u32,
        pub rgba: Vec<u8>,
    }

    // wl_shm format codes (wl_shm.format: 0/1 are ARGB/XRGB8888, the rest are DRM fourcc).
    pub const ARGB8888: u32 = 0;
    pub const XRGB8888: u32 = 1;
    pub const XBGR8888: u32 = 0x3432_4258;
    pub const ABGR8888: u32 = 0x3432_4241;
    pub const XRGB2101010: u32 = 0x3033_5258;
    pub const ARGB2101010: u32 = 0x3033_5241;
    pub const XBGR2101010: u32 = 0x3033_4258;
    pub const ABGR2101010: u32 = 0x3033_4241;

    fn ten(c: u32) -> u8 {
        ((c & 0x3ff) * 255 + 511).div_euclid(1023) as u8
    }

    impl Pixels<'_> {
        /// Size of the buffer once its transform is applied (the upright, displayed orientation).
        pub fn upright(&self) -> (u32, u32) {
            if self.transform % 2 == 1 {
                (self.height, self.width)
            } else {
                (self.width, self.height)
            }
        }

        /// RGBA of buffer pixel (x, y) in buffer coordinates.
        pub fn at(&self, x: u32, y: u32, opaque: bool) -> [u8; 4] {
            let y = if self.y_invert { self.height - 1 - y } else { y };
            let o = y as usize * self.stride as usize + x as usize * 4;
            let p = &self.data[o..o + 4];
            let v = u32::from_le_bytes([p[0], p[1], p[2], p[3]]);
            let (r, g, b, a) = match self.format {
                XRGB8888 => (p[2], p[1], p[0], 255),
                ARGB8888 => (p[2], p[1], p[0], p[3]),
                XBGR8888 => (p[0], p[1], p[2], 255),
                ABGR8888 => (p[0], p[1], p[2], p[3]),
                XRGB2101010 | ARGB2101010 => {
                    let a = if self.format == ARGB2101010 { ((v >> 30) * 85) as u8 } else { 255 };
                    (ten(v >> 20), ten(v >> 10), ten(v), a)
                }
                XBGR2101010 | ABGR2101010 => {
                    let a = if self.format == ABGR2101010 { ((v >> 30) * 85) as u8 } else { 255 };
                    (ten(v), ten(v >> 10), ten(v >> 20), a)
                }
                _ => (0, 0, 0, 0),
            };
            [r, g, b, if opaque { 255 } else { a }]
        }

        /// Buffer pixel shown at upright position (u, v) in [0,1)^2 of the displayed image. The buffer holds the
        /// contents before `transform` (wl_output.transform semantics: 90 = rotated 90 degrees counter-clockwise).
        pub fn sample(&self, u: f64, v: f64, opaque: bool) -> [u8; 4] {
            let (bw, bh) = (self.width as f64, self.height as f64);
            let (dw, dh) = if self.transform % 2 == 1 { (bh, bw) } else { (bw, bh) };
            let (dx, dy) = (u * dw, v * dh);
            // Verified against grim and the compositor's logical layout for all eight transforms (nested Hyprland).
            let (x, y) = match self.transform {
                1 => (dy, bh - dx),
                2 => (bw - dx, bh - dy),
                3 => (bw - dy, dx),
                4 => (bw - dx, dy),
                5 => (dy, dx),
                6 => (dx, bh - dy),
                7 => (bw - dy, bh - dx),
                _ => (dx, dy),
            };
            let xi = (x.floor() as i64).clamp(0, self.width as i64 - 1) as u32;
            let yi = (y.floor() as i64).clamp(0, self.height as i64 - 1) as u32;
            self.at(xi, yi, opaque)
        }

        /// True when every pixel is pure black (the "permission denied" frame Hyprland hands out under enforcement).
        pub fn all_black(&self) -> bool {
            (0..self.height).all(|y| (0..self.width).all(|x| self.at(x, y, true)[..3] == [0, 0, 0]))
        }
    }

    /// Pixel bounds of `r` at scale `s`, edges rounded outward.
    pub fn pixel_bounds(r: &Rect, s: f64) -> (i64, i64, i64, i64) {
        let eps = 1e-6;
        let x0 = (r.x * s + eps).floor() as i64;
        let y0 = (r.y * s + eps).floor() as i64;
        let x1 = (((r.x + r.w) * s) - eps).ceil() as i64;
        let y1 = (((r.y + r.h) * s) - eps).ceil() as i64;
        (x0, y0, x1.max(x0 + 1), y1.max(y0 + 1))
    }

    /// Render the logical rectangle `r` at scale `s` from the placed buffers (uncovered pixels stay transparent).
    pub fn compose(r: &Rect, s: f64, srcs: &[Placed]) -> Canvas {
        let (x0, y0, x1, y1) = pixel_bounds(r, s);
        let (w, h) = ((x1 - x0) as u32, (y1 - y0) as u32);
        let mut rgba = vec![0u8; w as usize * h as usize * 4];
        for src in srcs {
            let area = Rect { x: src.x, y: src.y, w: src.w, h: src.h };
            let Some(hit) = area.intersect(&Rect { x: x0 as f64 / s, y: y0 as f64 / s, w: w as f64 / s, h: h as f64 / s }) else {
                continue;
            };
            let (hx0, hy0, hx1, hy1) = pixel_bounds(&hit, s);
            for py in hy0.max(y0)..hy1.min(y1) {
                let ly = (py as f64 + 0.5) / s;
                if ly < src.y || ly >= src.y + src.h {
                    continue;
                }
                let v = (ly - src.y) / src.h;
                for px in hx0.max(x0)..hx1.min(x1) {
                    let lx = (px as f64 + 0.5) / s;
                    if lx < src.x || lx >= src.x + src.w {
                        continue;
                    }
                    let u = (lx - src.x) / src.w;
                    let c = src.px.sample(u, v, src.opaque);
                    let o = ((py - y0) as usize * w as usize + (px - x0) as usize) * 4;
                    rgba[o..o + 4].copy_from_slice(&c);
                }
            }
        }
        Canvas { w, h, rgba }
    }

    /// The whole buffer, upright (a toplevel capture at its own scale).
    pub fn upright(px: &Pixels, opaque: bool) -> Canvas {
        let (w, h) = px.upright();
        let mut rgba = Vec::with_capacity(w as usize * h as usize * 4);
        for y in 0..h {
            for x in 0..w {
                rgba.extend_from_slice(&px.sample((x as f64 + 0.5) / w as f64, (y as f64 + 0.5) / h as f64, opaque));
            }
        }
        Canvas { w, h, rgba }
    }

    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    pub enum Format {
        Png,
        Jpg,
        Webp,
    }

    impl Format {
        pub fn parse(s: &str) -> Result<Format, String> {
            match s.to_ascii_lowercase().as_str() {
                "png" => Ok(Format::Png),
                "jpg" | "jpeg" => Ok(Format::Jpg),
                "webp" => Ok(Format::Webp),
                o => Err(format!("unknown format {o:?} (png, jpg or webp)")),
            }
        }
        pub fn ext(self) -> &'static str {
            match self {
                Format::Png => "png",
                Format::Jpg => "jpg",
                Format::Webp => "webp",
            }
        }
    }

    /// Encode the canvas. `quality` (1..=100) is the JPEG quality; WebP is lossless; `compression` picks PNG's
    /// fast / default / best deflate.
    pub fn encode(c: &Canvas, fmt: Format, quality: u8, compression: &str) -> Result<Vec<u8>, String> {
        use image::ImageEncoder;
        let mut out = Vec::new();
        match fmt {
            Format::Png => {
                use image::codecs::png::{CompressionType, FilterType, PngEncoder};
                let ct = match compression {
                    "best" => CompressionType::Best,
                    "default" => CompressionType::Default,
                    _ => CompressionType::Fast,
                };
                PngEncoder::new_with_quality(&mut out, ct, FilterType::Adaptive)
                    .write_image(&c.rgba, c.w, c.h, image::ExtendedColorType::Rgba8)
                    .map_err(|e| format!("png: {e}"))?;
            }
            Format::Jpg => {
                // JPEG has no alpha: uncovered pixels (between outputs) become black.
                let rgb: Vec<u8> = c
                    .rgba
                    .chunks_exact(4)
                    .flat_map(|p| {
                        let a = p[3] as u32;
                        [(p[0] as u32 * a / 255) as u8, (p[1] as u32 * a / 255) as u8, (p[2] as u32 * a / 255) as u8]
                    })
                    .collect();
                image::codecs::jpeg::JpegEncoder::new_with_quality(&mut out, quality.clamp(1, 100))
                    .write_image(&rgb, c.w, c.h, image::ExtendedColorType::Rgb8)
                    .map_err(|e| format!("jpg: {e}"))?;
            }
            Format::Webp => {
                image::codecs::webp::WebPEncoder::new_lossless(&mut out)
                    .write_image(&c.rgba, c.w, c.h, image::ExtendedColorType::Rgba8)
                    .map_err(|e| format!("webp: {e}"))?;
            }
        }
        Ok(out)
    }

    /// Downscaled RGBA copy no larger than `max` on its long side (notification thumbnails).
    pub fn thumbnail(c: &Canvas, max: u32) -> Canvas {
        let long = c.w.max(c.h).max(1);
        if long <= max {
            return Canvas { w: c.w, h: c.h, rgba: c.rgba.clone() };
        }
        let (w, h) = (((c.w as u64 * max as u64) / long as u64).max(1) as u32, ((c.h as u64 * max as u64) / long as u64).max(1) as u32);
        let mut rgba = Vec::with_capacity(w as usize * h as usize * 4);
        for y in 0..h {
            let sy = ((y as u64 * c.h as u64) / h as u64) as usize;
            for x in 0..w {
                let sx = ((x as u64 * c.w as u64) / w as u64) as usize;
                let o = (sy * c.w as usize + sx) * 4;
                rgba.extend_from_slice(&c.rgba[o..o + 4]);
            }
        }
        Canvas { w, h, rgba }
    }

    /// strftime(3) in the local time zone.
    #[allow(deprecated)] // libc::time_t: 64-bit on x86_64 musl already
    pub fn strftime(fmt: &str, t: i64) -> String {
        let Ok(cfmt) = std::ffi::CString::new(fmt) else { return fmt.to_string() };
        // SAFETY: tm is plain data; localtime_r fills it; strftime writes at most buf.len() bytes.
        unsafe {
            let mut tm: libc::tm = std::mem::zeroed();
            let tt: libc::time_t = t as libc::time_t;
            if libc::localtime_r(&tt, &mut tm).is_null() {
                return fmt.to_string();
            }
            let mut buf = vec![0u8; 512];
            let n = libc::strftime(buf.as_mut_ptr() as *mut libc::c_char, buf.len(), cfmt.as_ptr(), &tm);
            buf.truncate(n);
            String::from_utf8_lossy(&buf).into_owned()
        }
    }

    /// A file-name-safe token: letters, digits, '.', '_' and '-' only.
    pub fn sanitize(s: &str) -> String {
        let out: String = s
            .chars()
            .map(|c| if c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-') { c } else { '_' })
            .collect();
        let out = out.trim_matches(|c| c == '.' || c == '_').to_string();
        if out.is_empty() { "screen".into() } else { out.chars().take(64).collect() }
    }

    /// Render a [desktop.screenshot] filename template (no extension: the format adds it).
    pub fn render_name(template: &str, t: i64, mode: &str, output: &str, app: &str) -> String {
        let base = strftime(template, t)
            .replace("{mode}", &sanitize(mode))
            .replace("{output}", &sanitize(output))
            .replace("{app}", &sanitize(app));
        let base: String = base.chars().map(|c| if c == '/' || c == '\0' { '-' } else { c }).collect();
        let base = base.trim_start_matches('.').to_string();
        if base.is_empty() { "MiOS-screenshot".into() } else { base }
    }

    /// Write `bytes` as <dir>/<base>.<ext> (or <base>-N.<ext> when taken) atomically and without clobbering.
    pub fn save_atomic(dir: &Path, base: &str, ext: &str, bytes: &[u8]) -> Result<PathBuf, String> {
        use std::os::unix::fs::OpenOptionsExt;
        std::fs::create_dir_all(dir).map_err(|e| format!("cannot create {}: {e}", dir.display()))?;
        let tmp = dir.join(format!(".{base}.{}.part", std::process::id()));
        {
            let mut f = std::fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .mode(0o644)
                .open(&tmp)
                .map_err(|e| format!("cannot write {}: {e}", tmp.display()))?;
            let r = f.write_all(bytes).and_then(|_| f.sync_all());
            if let Err(e) = r {
                let _ = std::fs::remove_file(&tmp);
                return Err(format!("cannot write {}: {e}", tmp.display()));
            }
        }
        for n in 0..10_000u32 {
            let name = if n == 0 { format!("{base}.{ext}") } else { format!("{base}-{n}.{ext}") };
            let target = dir.join(&name);
            match rustix::fs::renameat_with(rustix::fs::CWD, &tmp, rustix::fs::CWD, &target, rustix::fs::RenameFlags::NOREPLACE) {
                Ok(()) => return Ok(target),
                Err(rustix::io::Errno::EXIST) => continue,
                Err(rustix::io::Errno::INVAL) | Err(rustix::io::Errno::NOSYS) => {
                    // No RENAME_NOREPLACE on this filesystem: link() refuses to clobber too.
                    match std::fs::hard_link(&tmp, &target) {
                        Ok(()) => {
                            let _ = std::fs::remove_file(&tmp);
                            return Ok(target);
                        }
                        Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => continue,
                        Err(e) => {
                            let _ = std::fs::remove_file(&tmp);
                            return Err(format!("cannot save {}: {e}", target.display()));
                        }
                    }
                }
                Err(e) => {
                    let _ = std::fs::remove_file(&tmp);
                    return Err(format!("cannot save {}: {e}", target.display()));
                }
            }
        }
        let _ = std::fs::remove_file(&tmp);
        Err(format!("no free file name for {base}.{ext} in {}", dir.display()))
    }

    /// Atomically replace `path` (an explicit --file target).
    pub fn write_replace(path: &Path, bytes: &[u8]) -> Result<(), String> {
        use std::os::unix::fs::OpenOptionsExt;
        let dir = path.parent().filter(|p| !p.as_os_str().is_empty()).unwrap_or(Path::new("."));
        let name = path.file_name().ok_or_else(|| format!("{} is not a file path", path.display()))?;
        let tmp = dir.join(format!(".{}.{}.part", name.to_string_lossy(), std::process::id()));
        let mut f = std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o644)
            .open(&tmp)
            .map_err(|e| format!("cannot write {}: {e}", tmp.display()))?;
        let r = f.write_all(bytes).and_then(|_| f.sync_all()).and_then(|_| std::fs::rename(&tmp, path));
        if let Err(e) = r {
            let _ = std::fs::remove_file(&tmp);
            return Err(format!("cannot write {}: {e}", path.display()));
        }
        Ok(())
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        fn buf(w: u32, h: u32, f: impl Fn(u32, u32) -> [u8; 3]) -> Vec<u8> {
            let mut v = Vec::new();
            for y in 0..h {
                for x in 0..w {
                    let [r, g, b] = f(x, y);
                    v.extend_from_slice(&[b, g, r, 0xff]); // XRGB8888 little endian
                }
            }
            v
        }

        fn px(data: &[u8], w: u32, h: u32, transform: u32) -> Pixels<'_> {
            Pixels { width: w, height: h, stride: w * 4, format: XRGB8888, transform, y_invert: false, data }
        }

        #[test]
        fn crop_inside_one_output_is_exact() {
            let d = buf(40, 20, |x, y| [x as u8, y as u8, 7]);
            let p = Placed { px: px(&d, 40, 20, 0), x: 100.0, y: 50.0, w: 40.0, h: 20.0, opaque: true };
            let c = compose(&Rect { x: 110.0, y: 55.0, w: 5.0, h: 3.0 }, 1.0, &[p]);
            assert_eq!((c.w, c.h), (5, 3));
            assert_eq!(&c.rgba[0..4], &[10, 5, 7, 255]);
            let last = ((2 * 5) + 4) * 4;
            assert_eq!(&c.rgba[last..last + 4], &[14, 7, 7, 255]);
        }

        #[test]
        fn fractional_scale_region_is_logical_times_scale() {
            // A 2.5x output: 1100x2080 buffer, 440x832 logical at (2000, 0).
            let d = buf(1100, 2080, |x, y| [(x % 251) as u8, (y % 241) as u8, 1]);
            let p = Placed { px: px(&d, 1100, 2080, 0), x: 2000.0, y: 0.0, w: 440.0, h: 832.0, opaque: true };
            let c = compose(&Rect { x: 2010.0, y: 20.0, w: 100.0, h: 40.0 }, 2.5, &[p]);
            assert_eq!((c.w, c.h), (250, 100));
            // canvas (0,0) = buffer (25, 50)
            assert_eq!(&c.rgba[0..4], &[25, 50, 1, 255]);
        }

        #[test]
        fn mixed_scales_use_the_largest_and_cover_both() {
            let a = buf(100, 100, |_, _| [255, 0, 0]);
            let b = buf(200, 200, |_, _| [0, 0, 255]);
            let pa = Placed { px: px(&a, 100, 100, 0), x: 0.0, y: 0.0, w: 100.0, h: 100.0, opaque: true };
            let pb = Placed { px: px(&b, 200, 200, 0), x: 100.0, y: 0.0, w: 100.0, h: 100.0, opaque: true };
            let r = Rect { x: 90.0, y: 0.0, w: 20.0, h: 10.0 };
            let c = compose(&r, 2.0, &[pa, pb]);
            assert_eq!((c.w, c.h), (40, 20));
            assert_eq!(&c.rgba[0..4], &[255, 0, 0, 255]);
            let o = 39 * 4;
            assert_eq!(&c.rgba[o..o + 4], &[0, 0, 255, 255]);
        }

        #[test]
        fn uncovered_space_is_transparent() {
            let a = buf(10, 10, |_, _| [9, 9, 9]);
            let pa = Placed { px: px(&a, 10, 10, 0), x: 0.0, y: 0.0, w: 10.0, h: 10.0, opaque: true };
            let c = compose(&Rect { x: 0.0, y: 0.0, w: 20.0, h: 10.0 }, 1.0, &[pa]);
            assert_eq!(c.rgba[3], 255);
            assert_eq!(c.rgba[(15 * 4) + 3], 0);
        }

        #[test]
        fn transforms_round_trip_corners() {
            // Buffer 4x2 with a marked corner at buffer (0,0).
            let d = buf(4, 2, |x, y| if (x, y) == (0, 0) { [255, 255, 255] } else { [0, 0, 0] });
            for t in 0..8u32 {
                let p = px(&d, 4, 2, t);
                let c = upright(&p, true);
                let (w, h) = p.upright();
                assert_eq!((c.w, c.h), (w, h), "transform {t}");
                let white: Vec<(u32, u32)> = (0..h)
                    .flat_map(|y| (0..w).map(move |x| (x, y)))
                    .filter(|&(x, y)| c.rgba[((y * w + x) * 4) as usize] == 255)
                    .collect();
                assert_eq!(white.len(), 1, "transform {t}: exactly one marked pixel");
            }
        }

        #[test]
        fn ten_bit_formats_convert_down() {
            let v: u32 = (1023 << 20) | (512 << 10) | 0; // XRGB2101010: r=1023 g=512 b=0
            let d = v.to_le_bytes();
            let p = Pixels { width: 1, height: 1, stride: 4, format: XRGB2101010, transform: 0, y_invert: false, data: &d };
            assert_eq!(p.at(0, 0, true), [255, 128, 0, 255]);
            let p = Pixels { format: XBGR2101010, ..p };
            assert_eq!(p.at(0, 0, true), [0, 128, 255, 255]);
        }

        #[test]
        fn names_render_and_sanitize() {
            let n = render_name("MiOS-%Y-{mode}-{output}-{app}", 0, "region", "HDMI-A-1", "org/gnome Nautilus");
            assert!(n.starts_with("MiOS-19") || n.starts_with("MiOS-20"), "{n}");
            assert!(n.ends_with("-region-HDMI-A-1-org_gnome_Nautilus"), "{n}");
            assert_eq!(render_name("../../{app}", 0, "", "", ".."), "-..-screen");
            assert_eq!(sanitize("../.."), "screen");
        }

        #[test]
        fn save_never_clobbers() {
            let dir = std::env::temp_dir().join(format!("mios-shot-test-{}", std::process::id()));
            let _ = std::fs::remove_dir_all(&dir);
            let a = save_atomic(&dir, "shot", "png", b"one").unwrap();
            let b = save_atomic(&dir, "shot", "png", b"two").unwrap();
            let c = save_atomic(&dir, "shot", "png", b"three").unwrap();
            assert_eq!(a.file_name().unwrap(), "shot.png");
            assert_eq!(b.file_name().unwrap(), "shot-1.png");
            assert_eq!(c.file_name().unwrap(), "shot-2.png");
            assert_eq!(std::fs::read(&a).unwrap(), b"one");
            assert_eq!(std::fs::read(&b).unwrap(), b"two");
            let left: Vec<_> = std::fs::read_dir(&dir).unwrap().filter_map(|e| e.ok()).filter(|e| e.file_name().to_string_lossy().ends_with(".part")).collect();
            assert!(left.is_empty(), "no temp files left");
            let _ = std::fs::remove_dir_all(&dir);
        }

        #[test]
        fn encodes_all_formats() {
            let c = Canvas { w: 3, h: 2, rgba: vec![200; 24] };
            let png = encode(&c, Format::Png, 90, "fast").unwrap();
            assert_eq!(&png[1..4], b"PNG");
            let jpg = encode(&c, Format::Jpg, 90, "fast").unwrap();
            assert_eq!(&jpg[0..2], &[0xff, 0xd8]);
            let webp = encode(&c, Format::Webp, 90, "fast").unwrap();
            assert_eq!(&webp[8..12], b"WEBP");
        }

        #[test]
        fn thumbnail_keeps_aspect() {
            let c = Canvas { w: 1000, h: 500, rgba: vec![1; 1000 * 500 * 4] };
            let t = thumbnail(&c, 256);
            assert_eq!((t.w, t.h), (256, 128));
        }
    }
}

mod wl {
    //! AI-hint: Wayland side of mios-shot: binds outputs (wl_output + xdg-output logical geometry), captures them or one toplevel
    //! into shm buffers through ext-image-copy-capture-v1 (falling back to wlr-screencopy-v1 for outputs), shows the captured
    //! buffers as the frozen frame (one layer-shell overlay surface per output, namespace mios-shot-freeze, no input, viewporter
    //! to the logical size), and serves the clipboard through ext-data-control-v1. Colour-management unaware on purpose, so
    //! Hyprland hands back SDR frames; shm only (a still capture needs a CPU copy anyway; dmabuf would need gbm/EGL); never
    //! opens a cursor session (paint_cursors only).
    //! AI-related: usr/share/mios/mios.toml [desktop.screenshot]

    use memmap2::MmapMut;
    use std::fs::File;
    use std::io::Write;
    use std::os::fd::{AsFd, AsRawFd, OwnedFd};
    use std::time::{Duration, Instant};
    use wayland_client::globals::{registry_queue_init, GlobalListContents};
    use wayland_client::protocol::{
        wl_buffer, wl_compositor, wl_output, wl_region, wl_registry, wl_seat, wl_shm, wl_shm_pool,
        wl_surface,
    };
    use wayland_client::{delegate_noop, event_created_child, Connection, Dispatch, EventQueue, Proxy, QueueHandle, WEnum};
    use wayland_protocols::ext::data_control::v1::client::{
        ext_data_control_device_v1::{self, ExtDataControlDeviceV1},
        ext_data_control_manager_v1::ExtDataControlManagerV1,
        ext_data_control_offer_v1::ExtDataControlOfferV1,
        ext_data_control_source_v1::{self, ExtDataControlSourceV1},
    };
    use wayland_protocols::ext::foreign_toplevel_list::v1::client::{
        ext_foreign_toplevel_handle_v1::{self, ExtForeignToplevelHandleV1},
        ext_foreign_toplevel_list_v1::{self, ExtForeignToplevelListV1},
    };
    use wayland_protocols::ext::image_capture_source::v1::client::{
        ext_foreign_toplevel_image_capture_source_manager_v1::ExtForeignToplevelImageCaptureSourceManagerV1,
        ext_image_capture_source_v1::ExtImageCaptureSourceV1,
        ext_output_image_capture_source_manager_v1::ExtOutputImageCaptureSourceManagerV1,
    };
    use wayland_protocols::ext::image_copy_capture::v1::client::{
        ext_image_copy_capture_frame_v1::{self, ExtImageCopyCaptureFrameV1},
        ext_image_copy_capture_manager_v1::{self, ExtImageCopyCaptureManagerV1},
        ext_image_copy_capture_session_v1::{self, ExtImageCopyCaptureSessionV1},
    };
    use wayland_protocols::wp::viewporter::client::{wp_viewport::WpViewport, wp_viewporter::WpViewporter};
    use wayland_protocols::xdg::xdg_output::zv1::client::{
        zxdg_output_manager_v1::ZxdgOutputManagerV1,
        zxdg_output_v1::{self, ZxdgOutputV1},
    };
    use wayland_protocols_wlr::layer_shell::v1::client::{
        zwlr_layer_shell_v1::{self, ZwlrLayerShellV1},
        zwlr_layer_surface_v1::{self, ZwlrLayerSurfaceV1},
    };
    use wayland_protocols_wlr::screencopy::v1::client::{
        zwlr_screencopy_frame_v1::{self, ZwlrScreencopyFrameV1},
        zwlr_screencopy_manager_v1::ZwlrScreencopyManagerV1,
    };

    /// shm formats mios-shot can read, in preference order (8-bit first; 10-bit is converted down).
    const FORMATS: [wl_shm::Format; 8] = [
        wl_shm::Format::Xrgb8888,
        wl_shm::Format::Argb8888,
        wl_shm::Format::Xbgr8888,
        wl_shm::Format::Abgr8888,
        wl_shm::Format::Xrgb2101010,
        wl_shm::Format::Argb2101010,
        wl_shm::Format::Xbgr2101010,
        wl_shm::Format::Abgr2101010,
    ];
    /// How long one capture or configure round may take before it is reported as a failure.
    const ROUND: Duration = Duration::from_secs(5);
    /// Once one output's frame is in, how much longer the others get.
    const GRACE: Duration = Duration::from_millis(750);

    #[derive(Debug, Clone, Default)]
    pub struct Output {
        pub name: String,
        pub description: String,
        /// Logical (compositor-space) geometry: xdg-output, else mode / integer scale.
        pub x: i32,
        pub y: i32,
        pub w: i32,
        pub h: i32,
        pub mode_w: i32,
        pub mode_h: i32,
        pub scale_int: i32,
        pub transform: u32,
        xdg_size: bool,
    }

    /// One captured image: the shm buffer the compositor filled, in buffer coordinates.
    pub struct Frame {
        pub width: u32,
        pub height: u32,
        pub stride: u32,
        pub format: wl_shm::Format,
        /// wl_output.transform the compositor applied to the buffer contents (applied again to show it upright).
        pub transform: u32,
        pub y_invert: bool,
        pub map: MmapMut,
        pub buffer: wl_buffer::WlBuffer,
        pool: wl_shm_pool::WlShmPool,
        _fd: OwnedFd,
    }

    impl Drop for Frame {
        fn drop(&mut self) {
            self.buffer.destroy();
            self.pool.destroy();
        }
    }

    #[derive(Default)]
    struct Pending {
        formats: Vec<wl_shm::Format>,
        size: Option<(u32, u32)>,
        constraints: bool,
        wlr_buffer: Option<(wl_shm::Format, u32, u32, u32)>,
        wlr_buffer_done: bool,
        transform: u32,
        y_invert: bool,
        ready: bool,
        failed: Option<String>,
    }

    #[derive(Default, Clone)]
    pub struct Toplevel {
        pub identifier: String,
        pub app_id: String,
        pub title: String,
        pub closed: bool,
    }

    struct Layer {
        surface: wl_surface::WlSurface,
        layer: ZwlrLayerSurfaceV1,
        viewport: Option<WpViewport>,
        configured: bool,
        closed: bool,
    }

    #[derive(Default)]
    struct Clip {
        offers: Vec<(String, std::sync::Arc<Vec<u8>>)>,
        cancelled: bool,
        sends: usize,
    }

    pub struct State {
        pub outputs: Vec<Output>,
        wl_outputs: Vec<wl_output::WlOutput>,
        pub globals: Vec<(String, u32)>,
        shm: Option<wl_shm::WlShm>,
        compositor: Option<wl_compositor::WlCompositor>,
        seat: Option<wl_seat::WlSeat>,
        layer_shell: Option<ZwlrLayerShellV1>,
        viewporter: Option<WpViewporter>,
        ext_copy: Option<ExtImageCopyCaptureManagerV1>,
        ext_output_src: Option<ExtOutputImageCaptureSourceManagerV1>,
        ext_toplevel_src: Option<ExtForeignToplevelImageCaptureSourceManagerV1>,
        toplevel_list: Option<ExtForeignToplevelListV1>,
        wlr_copy: Option<ZwlrScreencopyManagerV1>,
        data_control: Option<ExtDataControlManagerV1>,
        pending: Vec<Pending>,
        toplevel_handles: Vec<(ExtForeignToplevelHandleV1, Toplevel)>,
        layers: Vec<Layer>,
        clip: Clip,
    }

    pub struct Wl {
        _conn: Connection,
        globals: wayland_client::globals::GlobalList,
        queue: EventQueue<State>,
        qh: QueueHandle<State>,
        pub st: State,
    }

    /// Which capture protocol produced (or should produce) the frames.
    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    pub enum Backend {
        Ext,
        Wlr,
    }

    impl Backend {
        pub fn name(self) -> &'static str {
            match self {
                Backend::Ext => "ext-image-copy-capture-v1",
                Backend::Wlr => "wlr-screencopy-unstable-v1",
            }
        }
    }

    pub enum Source<'a> {
        Output(usize),
        Toplevel(&'a str),
    }

    impl Wl {
        pub fn connect() -> Result<Wl, String> {
            let conn = Connection::connect_to_env().map_err(|e| format!("no Wayland display ({e})"))?;
            let (globals, queue) =
                registry_queue_init::<State>(&conn).map_err(|e| format!("Wayland registry: {e}"))?;
            let qh = queue.handle();
            let list = globals.contents().clone_list();
            let mut st = State {
                outputs: Vec::new(),
                wl_outputs: Vec::new(),
                globals: list.iter().map(|g| (g.interface.clone(), g.version)).collect(),
                shm: globals.bind(&qh, 1..=1, ()).ok(),
                compositor: globals.bind(&qh, 4..=6, ()).ok(),
                seat: globals.bind(&qh, 1..=7, ()).ok(),
                layer_shell: globals.bind(&qh, 1..=4, ()).ok(),
                viewporter: globals.bind(&qh, 1..=1, ()).ok(),
                ext_copy: globals.bind(&qh, 1..=1, ()).ok(),
                ext_output_src: globals.bind(&qh, 1..=1, ()).ok(),
                ext_toplevel_src: globals.bind(&qh, 1..=1, ()).ok(),
                toplevel_list: None,
                wlr_copy: globals.bind(&qh, 1..=3, ()).ok(),
                data_control: globals.bind(&qh, 1..=1, ()).ok(),
                pending: Vec::new(),
                toplevel_handles: Vec::new(),
                layers: Vec::new(),
                clip: Clip::default(),
            };
            let xdg: Option<ZxdgOutputManagerV1> = globals.bind(&qh, 2..=3, ()).ok();
            for g in list.iter().filter(|g| g.interface == wl_output::WlOutput::interface().name) {
                let idx = st.outputs.len();
                let out: wl_output::WlOutput = globals.registry().bind(g.name, g.version.min(4), &qh, idx);
                if let Some(x) = &xdg {
                    x.get_xdg_output(&out, &qh, idx);
                }
                st.outputs.push(Output { scale_int: 1, ..Default::default() });
                st.wl_outputs.push(out);
            }
            let mut wl = Wl { _conn: conn, globals, queue, qh, st };
            wl.roundtrip()?;
            wl.roundtrip()?;
            for o in wl.st.outputs.iter_mut() {
                if !o.xdg_size {
                    // No xdg-output: the mode at the integer scale, rotated by the transform.
                    let s = o.scale_int.max(1);
                    let (w, h) = if o.transform % 2 == 1 { (o.mode_h, o.mode_w) } else { (o.mode_w, o.mode_h) };
                    o.w = w / s;
                    o.h = h / s;
                }
            }
            Ok(wl)
        }

        pub fn roundtrip(&mut self) -> Result<(), String> {
            self.queue.roundtrip(&mut self.st).map(|_| ()).map_err(|e| format!("Wayland: {e}"))
        }

        /// Dispatch whatever arrives within `ms` milliseconds (false on timeout). Polls the display fd so a
        /// compositor that never answers cannot hang the tool.
        pub fn dispatch_timeout(&mut self, ms: i32) -> Result<bool, String> {
            self.queue.flush().map_err(|e| format!("Wayland flush: {e}"))?;
            if self.queue.dispatch_pending(&mut self.st).map_err(|e| format!("Wayland: {e}"))? > 0 {
                return Ok(true);
            }
            if let Some(guard) = self.queue.prepare_read() {
                let fd = guard.connection_fd().as_raw_fd();
                let mut pfd = libc::pollfd { fd, events: libc::POLLIN, revents: 0 };
                // SAFETY: one valid pollfd for the duration of the call.
                let n = unsafe { libc::poll(&mut pfd, 1, ms) };
                if n <= 0 {
                    drop(guard);
                    return Ok(false);
                }
                match guard.read() {
                    Ok(_) => {}
                    // A wakeup with only part of a message buffered: nothing to dispatch yet.
                    Err(wayland_client::backend::WaylandError::Io(e)) if e.kind() == std::io::ErrorKind::WouldBlock => return Ok(false),
                    Err(e) => return Err(format!("Wayland read: {e}")),
                }
            }
            self.queue.dispatch_pending(&mut self.st).map_err(|e| format!("Wayland: {e}"))?;
            Ok(true)
        }

        /// Raw display fd, for callers that poll it together with their own fds.
        pub fn display_fd(&self) -> i32 {
            self._conn.backend().poll_fd().as_raw_fd()
        }

        pub fn flush(&mut self) {
            let _ = self.queue.flush();
        }

        fn wait_until(&mut self, what: &str, mut done: impl FnMut(&State) -> bool) -> Result<(), String> {
            let start = Instant::now();
            while !done(&self.st) {
                if start.elapsed() > ROUND {
                    return Err(format!("compositor did not answer the {what} within {}s", ROUND.as_secs()));
                }
                self.dispatch_timeout(100)?;
            }
            Ok(())
        }

        /// Wait for every pending capture to finish. An output that is not being drawn (DPMS off, a hidden nested
        /// window, a stalled virtual display) never answers: once one frame is in, the rest get GRACE more, and
        /// whatever has not answered by then is reported failed instead of holding every other output hostage.
        fn settle(&mut self, n: usize) -> Result<(), String> {
            let start = Instant::now();
            let mut first: Option<Instant> = None;
            loop {
                let done = |p: &Pending| p.ready || p.failed.is_some();
                if self.st.pending.iter().take(n).all(done) {
                    return Ok(());
                }
                if first.is_none() && self.st.pending.iter().take(n).any(|p| p.ready) {
                    first = Some(Instant::now());
                }
                if start.elapsed() > ROUND || first.is_some_and(|f| f.elapsed() > GRACE) {
                    for p in self.st.pending.iter_mut().take(n).filter(|p| !p.ready && p.failed.is_none()) {
                        p.failed = Some("the output did not render a frame (off, or not being drawn)".into());
                    }
                    return Ok(());
                }
                self.dispatch_timeout(50)?;
            }
        }

        pub fn backend(&self, want: &str) -> Option<Backend> {
            let ext = self.st.ext_copy.is_some() && self.st.ext_output_src.is_some() && self.st.shm.is_some();
            let wlr = self.st.wlr_copy.is_some() && self.st.shm.is_some();
            match want {
                "ext" => ext.then_some(Backend::Ext),
                "wlr" => wlr.then_some(Backend::Wlr),
                _ if ext => Some(Backend::Ext),
                _ if wlr => Some(Backend::Wlr),
                _ => None,
            }
        }

        pub fn can_capture_toplevels(&self) -> bool {
            self.st.ext_copy.is_some() && self.st.ext_toplevel_src.is_some() && self.has_global("ext_foreign_toplevel_list_v1")
        }

        pub fn has_global(&self, name: &str) -> bool {
            self.st.globals.iter().any(|(n, _)| n == name)
        }

        pub fn can_freeze(&self) -> bool {
            self.st.layer_shell.is_some() && self.st.viewporter.is_some() && self.st.compositor.is_some()
        }

        /// The foreign toplevels (identifier, app id, title) the compositor lists.
        pub fn toplevels(&mut self) -> Result<Vec<Toplevel>, String> {
            if self.st.toplevel_list.is_none() {
                let list: ExtForeignToplevelListV1 = self
                    .globals
                    .bind(&self.qh, 1..=1, ())
                    .map_err(|_| "the compositor has no ext_foreign_toplevel_list_v1")?;
                self.st.toplevel_list = Some(list);
                self.roundtrip()?;
                self.roundtrip()?;
            }
            Ok(self.st.toplevel_handles.iter().filter(|(_, t)| !t.closed).map(|(_, t)| t.clone()).collect())
        }

        /// Capture each source once, all sessions in flight together so the frames are one moment. One result per
        /// source: a source that fails (or never renders) does not fail the others.
        pub fn capture(&mut self, sources: &[Source], cursor: bool, backend: Backend) -> Result<Vec<Result<Frame, String>>, String> {
            let shm = self.st.shm.clone().ok_or("the compositor has no wl_shm")?;
            self.st.pending = (0..sources.len()).map(|_| Pending::default()).collect();
            match backend {
                Backend::Ext => self.capture_ext(sources, cursor, &shm),
                Backend::Wlr => self.capture_wlr(sources, cursor, &shm),
            }
        }

        fn capture_ext(&mut self, sources: &[Source], cursor: bool, shm: &wl_shm::WlShm) -> Result<Vec<Result<Frame, String>>, String> {
            let mgr = self.st.ext_copy.clone().ok_or("no ext_image_copy_capture_manager_v1")?;
            let options = if cursor {
                ext_image_copy_capture_manager_v1::Options::PaintCursors
            } else {
                ext_image_copy_capture_manager_v1::Options::empty()
            };
            let mut handles = Vec::new();
            if sources.iter().any(|s| matches!(s, Source::Toplevel(_))) {
                self.toplevels()?;
            }
            for (i, s) in sources.iter().enumerate() {
                let src: ExtImageCaptureSourceV1 = match s {
                    Source::Output(o) => {
                        let m = self.st.ext_output_src.as_ref().ok_or("no ext_output_image_capture_source_manager_v1")?;
                        m.create_source(&self.st.wl_outputs[*o], &self.qh, ())
                    }
                    Source::Toplevel(id) => {
                        let m = self
                            .st
                            .ext_toplevel_src
                            .as_ref()
                            .ok_or("no ext_foreign_toplevel_image_capture_source_manager_v1")?;
                        let h = self
                            .st
                            .toplevel_handles
                            .iter()
                            .find(|(_, t)| !t.closed && t.identifier == *id)
                            .map(|(h, _)| h.clone())
                            .ok_or_else(|| format!("no toplevel with identifier {id}"))?;
                        m.create_source(&h, &self.qh, ())
                    }
                };
                let session = mgr.create_session(&src, options, &self.qh, i);
                handles.push((src, session));
            }
            let n = sources.len();
            self.wait_until("capture constraints", |st| st.pending.iter().take(n).all(|p| p.constraints || p.failed.is_some()))?;
            let mut frames: Vec<Result<Frame, String>> = Vec::new();
            let mut objs = Vec::new();
            for (i, (_, session)) in handles.iter().enumerate() {
                if let Some(f) = self.st.pending[i].failed.clone() {
                    frames.push(Err(f));
                    continue;
                }
                let Some((w, h)) = self.st.pending[i].size else {
                    frames.push(Err("capture session sent no buffer size".into()));
                    continue;
                };
                let Some(fmt) = FORMATS.iter().copied().find(|f| self.st.pending[i].formats.contains(f)) else {
                    frames.push(Err("the compositor offered no shm format mios-shot reads".into()));
                    continue;
                };
                let frame = alloc(shm, &self.qh, w, h, w * 4, fmt)?;
                let cf = session.create_frame(&self.qh, i);
                cf.attach_buffer(&frame.buffer);
                cf.damage_buffer(0, 0, w as i32, h as i32);
                cf.capture();
                frames.push(Ok(frame));
                objs.push(cf);
            }
            self.settle(n)?;
            for (i, f) in frames.iter_mut().enumerate() {
                if let Some(e) = self.st.pending[i].failed.clone() {
                    *f = Err(format!("capture failed: {e}"));
                } else if let Ok(fr) = f {
                    fr.transform = self.st.pending[i].transform;
                }
            }
            for cf in objs {
                cf.destroy();
            }
            for (src, session) in handles {
                session.destroy();
                src.destroy();
            }
            self.flush();
            Ok(frames)
        }

        fn capture_wlr(&mut self, sources: &[Source], cursor: bool, shm: &wl_shm::WlShm) -> Result<Vec<Result<Frame, String>>, String> {
            let mgr = self.st.wlr_copy.clone().ok_or("no zwlr_screencopy_manager_v1")?;
            let v3 = mgr.version() >= 3;
            let mut objs = Vec::new();
            for (i, s) in sources.iter().enumerate() {
                let Source::Output(o) = s else {
                    return Err("wlr-screencopy cannot capture a single window (needs ext-image-copy-capture)".into());
                };
                objs.push(mgr.capture_output(cursor as i32, &self.st.wl_outputs[*o], &self.qh, i));
            }
            let n = sources.len();
            // The buffer offer is protocol-only (no rendering): every output answers it promptly.
            self.wait_until("screencopy buffer", |st| {
                st.pending.iter().take(n).all(|p| p.failed.is_some() || (p.wlr_buffer.is_some() && (!v3 || p.wlr_buffer_done)))
            })?;
            let mut frames: Vec<Result<Frame, String>> = Vec::new();
            for (i, f) in objs.iter().enumerate() {
                if let Some(e) = self.st.pending[i].failed.clone() {
                    frames.push(Err(format!("screencopy failed: {e}")));
                    continue;
                }
                let Some((fmt, w, h, stride)) = self.st.pending[i].wlr_buffer else {
                    frames.push(Err("no screencopy shm buffer offered".into()));
                    continue;
                };
                if !FORMATS.contains(&fmt) {
                    frames.push(Err(format!("screencopy offered unsupported format {fmt:?}")));
                    continue;
                }
                let frame = alloc(shm, &self.qh, w, h, stride, fmt)?;
                f.copy(&frame.buffer);
                frames.push(Ok(frame));
            }
            self.settle(n)?;
            for (i, fr) in frames.iter_mut().enumerate() {
                if let Some(e) = self.st.pending[i].failed.clone() {
                    *fr = Err(format!("screencopy failed: {e}"));
                } else if let Ok(fr) = fr {
                    fr.y_invert = self.st.pending[i].y_invert;
                    // wlr-screencopy frames are in buffer orientation: the output's own transform shows them upright.
                    if let Source::Output(o) = sources[i] {
                        fr.transform = self.st.outputs[o].transform;
                    }
                }
            }
            for f in objs {
                f.destroy();
            }
            self.flush();
            Ok(frames)
        }

        /// Show `frames[i]` full-screen on output `outs[i]` as the frozen frame (overlay layer, no input, no keyboard).
        pub fn freeze(&mut self, outs: &[usize], frames: &[Frame]) -> Result<(), String> {
            let comp = self.st.compositor.clone().ok_or("no wl_compositor")?;
            let shell = self.st.layer_shell.clone().ok_or("no zwlr_layer_shell_v1")?;
            let vp = self.st.viewporter.clone().ok_or("no wp_viewporter")?;
            let base = self.st.layers.len();
            for (k, &o) in outs.iter().enumerate() {
                let surface = comp.create_surface(&self.qh, ());
                let layer = shell.get_layer_surface(
                    &surface,
                    Some(&self.st.wl_outputs[o]),
                    zwlr_layer_shell_v1::Layer::Overlay,
                    "mios-shot-freeze".into(),
                    &self.qh,
                    base + k,
                );
                use zwlr_layer_surface_v1::Anchor;
                layer.set_anchor(Anchor::Top | Anchor::Bottom | Anchor::Left | Anchor::Right);
                layer.set_exclusive_zone(-1);
                layer.set_keyboard_interactivity(zwlr_layer_surface_v1::KeyboardInteractivity::None);
                let region: wl_region::WlRegion = comp.create_region(&self.qh, ());
                surface.set_input_region(Some(&region));
                region.destroy();
                surface.commit();
                self.st.layers.push(Layer { surface, layer, viewport: None, configured: false, closed: false });
            }
            let n = outs.len();
            self.wait_until("freeze surface configure", |st| st.layers[base..base + n].iter().all(|l| l.configured || l.closed))?;
            for (k, &o) in outs.iter().enumerate() {
                let out = self.st.outputs[o].clone();
                let f = &frames[k];
                let l = &mut self.st.layers[base + k];
                if l.closed {
                    return Err(format!("the compositor closed the freeze surface on {}", out.name));
                }
                let viewport = vp.get_viewport(&l.surface, &self.qh, ());
                viewport.set_destination(out.w.max(1), out.h.max(1));
                let t = transform_enum(f.transform);
                let t = if f.y_invert { flip_y(t) } else { t };
                l.surface.set_buffer_transform(t);
                l.surface.attach(Some(&f.buffer), 0, 0);
                l.surface.damage_buffer(0, 0, i32::MAX, i32::MAX);
                l.surface.commit();
                l.viewport = Some(viewport);
            }
            self.roundtrip()
        }

        pub fn unfreeze(&mut self) {
            for l in self.st.layers.drain(..) {
                if let Some(v) = l.viewport {
                    v.destroy();
                }
                l.layer.destroy();
                l.surface.destroy();
            }
            let _ = self.queue.roundtrip(&mut self.st);
        }

        /// Own the clipboard with `offers` (mime type -> bytes) until another client replaces it. `ready` runs once the
        /// selection is set. Returns the number of paste requests served.
        pub fn serve_clipboard(
            &mut self,
            offers: Vec<(String, std::sync::Arc<Vec<u8>>)>,
            ready: impl FnOnce(),
        ) -> Result<usize, String> {
            let mgr = self.st.data_control.clone().ok_or("the compositor has no ext_data_control_manager_v1")?;
            let seat = self.st.seat.clone().ok_or("the compositor has no wl_seat")?;
            let device = mgr.get_data_device(&seat, &self.qh, ());
            let source = mgr.create_data_source(&self.qh, ());
            for (m, _) in &offers {
                source.offer(m.clone());
            }
            self.st.clip = Clip { offers, cancelled: false, sends: 0 };
            device.set_selection(Some(&source));
            self.roundtrip()?;
            ready();
            while !self.st.clip.cancelled {
                self.queue.blocking_dispatch(&mut self.st).map_err(|e| format!("Wayland: {e}"))?;
            }
            source.destroy();
            device.destroy();
            self.flush();
            Ok(self.st.clip.sends)
        }
    }

    pub fn transform_enum(t: u32) -> wl_output::Transform {
        use wl_output::Transform::*;
        match t {
            1 => _90,
            2 => _180,
            3 => _270,
            4 => Flipped,
            5 => Flipped90,
            6 => Flipped180,
            7 => Flipped270,
            _ => Normal,
        }
    }

    /// A y-inverted (wlr) buffer shown upright: compose the vertical flip with the transform.
    fn flip_y(t: wl_output::Transform) -> wl_output::Transform {
        use wl_output::Transform::*;
        match t {
            Normal => Flipped180,
            _90 => Flipped90,
            _180 => Flipped,
            _270 => Flipped270,
            Flipped => _180,
            Flipped90 => _90,
            Flipped180 => Normal,
            Flipped270 => _270,
            other => other,
        }
    }

    fn alloc(
        shm: &wl_shm::WlShm,
        qh: &QueueHandle<State>,
        w: u32,
        h: u32,
        stride: u32,
        format: wl_shm::Format,
    ) -> Result<Frame, String> {
        if w == 0 || h == 0 || stride < w * 4 {
            return Err(format!("invalid capture buffer {w}x{h} stride {stride}"));
        }
        let size = stride as usize * h as usize;
        let fd = rustix::fs::memfd_create("mios-shot", rustix::fs::MemfdFlags::CLOEXEC)
            .map_err(|e| format!("memfd_create: {e}"))?;
        let file = File::from(fd);
        file.set_len(size as u64).map_err(|e| format!("memfd size: {e}"))?;
        // SAFETY: the memfd is private to this process and the compositor writes only within `size`.
        let map = unsafe { MmapMut::map_mut(&file) }.map_err(|e| format!("mmap: {e}"))?;
        let pool = shm.create_pool(file.as_fd(), size as i32, qh, ());
        let buffer = pool.create_buffer(0, w as i32, h as i32, stride as i32, format, qh, ());
        Ok(Frame {
            width: w,
            height: h,
            stride,
            format,
            transform: 0,
            y_invert: false,
            map,
            buffer,
            pool,
            _fd: OwnedFd::from(file),
        })
    }

    // ---- event dispatch ----------------------------------------------------------------------------------

    impl Dispatch<wl_registry::WlRegistry, GlobalListContents> for State {
        fn event(_: &mut Self, _: &wl_registry::WlRegistry, _: wl_registry::Event, _: &GlobalListContents, _: &Connection, _: &QueueHandle<Self>) {}
    }

    impl Dispatch<wl_output::WlOutput, usize> for State {
        fn event(st: &mut Self, _: &wl_output::WlOutput, ev: wl_output::Event, idx: &usize, _: &Connection, _: &QueueHandle<Self>) {
            let o = &mut st.outputs[*idx];
            match ev {
                wl_output::Event::Geometry { transform: WEnum::Value(t), .. } => o.transform = t as u32,
                wl_output::Event::Mode { flags: WEnum::Value(f), width, height, .. } if f.contains(wl_output::Mode::Current) => {
                    o.mode_w = width;
                    o.mode_h = height;
                }
                wl_output::Event::Scale { factor } => o.scale_int = factor,
                wl_output::Event::Name { name } => o.name = name,
                wl_output::Event::Description { description } => o.description = description,
                _ => {}
            }
        }
    }

    impl Dispatch<ZxdgOutputV1, usize> for State {
        fn event(st: &mut Self, _: &ZxdgOutputV1, ev: zxdg_output_v1::Event, idx: &usize, _: &Connection, _: &QueueHandle<Self>) {
            let o = &mut st.outputs[*idx];
            match ev {
                zxdg_output_v1::Event::LogicalPosition { x, y } => {
                    o.x = x;
                    o.y = y;
                }
                zxdg_output_v1::Event::LogicalSize { width, height } => {
                    o.w = width;
                    o.h = height;
                    o.xdg_size = true;
                }
                zxdg_output_v1::Event::Name { name } if o.name.is_empty() => o.name = name,
                _ => {}
            }
        }
    }

    impl Dispatch<ExtImageCopyCaptureSessionV1, usize> for State {
        fn event(
            st: &mut Self,
            _: &ExtImageCopyCaptureSessionV1,
            ev: ext_image_copy_capture_session_v1::Event,
            idx: &usize,
            _: &Connection,
            _: &QueueHandle<Self>,
        ) {
            let p = &mut st.pending[*idx];
            use ext_image_copy_capture_session_v1::Event as E;
            match ev {
                E::BufferSize { width, height } => p.size = Some((width, height)),
                E::ShmFormat { format: WEnum::Value(f) } => p.formats.push(f),
                E::Done => p.constraints = true,
                E::Stopped => {
                    if !p.ready {
                        p.failed.get_or_insert_with(|| "capture session stopped (source gone or capture denied)".into());
                    }
                }
                _ => {}
            }
        }
    }

    impl Dispatch<ExtImageCopyCaptureFrameV1, usize> for State {
        fn event(
            st: &mut Self,
            _: &ExtImageCopyCaptureFrameV1,
            ev: ext_image_copy_capture_frame_v1::Event,
            idx: &usize,
            _: &Connection,
            _: &QueueHandle<Self>,
        ) {
            let p = &mut st.pending[*idx];
            use ext_image_copy_capture_frame_v1::Event as E;
            match ev {
                E::Transform { transform: WEnum::Value(t) } => p.transform = t as u32,
                E::Ready => p.ready = true,
                E::Failed { reason } => {
                    let why = match reason {
                        WEnum::Value(ext_image_copy_capture_frame_v1::FailureReason::BufferConstraints) => "buffer constraints changed",
                        WEnum::Value(ext_image_copy_capture_frame_v1::FailureReason::Stopped) => "session stopped",
                        _ => "unknown compositor error",
                    };
                    p.failed = Some(why.into());
                }
                _ => {}
            }
        }
    }

    impl Dispatch<ZwlrScreencopyFrameV1, usize> for State {
        fn event(
            st: &mut Self,
            _: &ZwlrScreencopyFrameV1,
            ev: zwlr_screencopy_frame_v1::Event,
            idx: &usize,
            _: &Connection,
            _: &QueueHandle<Self>,
        ) {
            let p = &mut st.pending[*idx];
            use zwlr_screencopy_frame_v1::Event as E;
            match ev {
                E::Buffer { format: WEnum::Value(f), width, height, stride } => {
                    if p.wlr_buffer.is_none() || FORMATS.contains(&f) {
                        p.wlr_buffer = Some((f, width, height, stride));
                    }
                }
                E::BufferDone => p.wlr_buffer_done = true,
                E::Flags { flags: WEnum::Value(f) } => p.y_invert = f.contains(zwlr_screencopy_frame_v1::Flags::YInvert),
                E::Ready { .. } => p.ready = true,
                E::Failed => p.failed = Some("compositor reported failure".into()),
                _ => {}
            }
        }
    }

    impl Dispatch<ExtForeignToplevelListV1, ()> for State {
        fn event(
            st: &mut Self,
            _: &ExtForeignToplevelListV1,
            ev: ext_foreign_toplevel_list_v1::Event,
            _: &(),
            _: &Connection,
            _: &QueueHandle<Self>,
        ) {
            if let ext_foreign_toplevel_list_v1::Event::Toplevel { toplevel } = ev {
                st.toplevel_handles.push((toplevel, Toplevel::default()));
            }
        }
        event_created_child!(State, ExtForeignToplevelListV1, [
            ext_foreign_toplevel_list_v1::EVT_TOPLEVEL_OPCODE => (ExtForeignToplevelHandleV1, ()),
        ]);
    }

    impl Dispatch<ExtForeignToplevelHandleV1, ()> for State {
        fn event(
            st: &mut Self,
            h: &ExtForeignToplevelHandleV1,
            ev: ext_foreign_toplevel_handle_v1::Event,
            _: &(),
            _: &Connection,
            _: &QueueHandle<Self>,
        ) {
            let Some((_, t)) = st.toplevel_handles.iter_mut().find(|(x, _)| x == h) else { return };
            use ext_foreign_toplevel_handle_v1::Event as E;
            match ev {
                E::Identifier { identifier } => t.identifier = identifier,
                E::AppId { app_id } => t.app_id = app_id,
                E::Title { title } => t.title = title,
                E::Closed => t.closed = true,
                _ => {}
            }
        }
    }

    impl Dispatch<ZwlrLayerSurfaceV1, usize> for State {
        fn event(
            st: &mut Self,
            layer: &ZwlrLayerSurfaceV1,
            ev: zwlr_layer_surface_v1::Event,
            idx: &usize,
            _: &Connection,
            _: &QueueHandle<Self>,
        ) {
            match ev {
                zwlr_layer_surface_v1::Event::Configure { serial, .. } => {
                    layer.ack_configure(serial);
                    if let Some(l) = st.layers.get_mut(*idx) {
                        l.configured = true;
                    }
                }
                zwlr_layer_surface_v1::Event::Closed => {
                    if let Some(l) = st.layers.get_mut(*idx) {
                        l.closed = true;
                    }
                }
                _ => {}
            }
        }
    }

    impl Dispatch<ExtDataControlSourceV1, ()> for State {
        fn event(
            st: &mut Self,
            _: &ExtDataControlSourceV1,
            ev: ext_data_control_source_v1::Event,
            _: &(),
            _: &Connection,
            _: &QueueHandle<Self>,
        ) {
            match ev {
                ext_data_control_source_v1::Event::Send { mime_type, fd } => {
                    if let Some((_, data)) = st.clip.offers.iter().find(|(m, _)| *m == mime_type) {
                        let mut f = File::from(fd);
                        // A paster that hangs up early is its own business; never die on EPIPE.
                        let _ = f.write_all(data);
                        st.clip.sends += 1;
                    }
                }
                ext_data_control_source_v1::Event::Cancelled => st.clip.cancelled = true,
                _ => {}
            }
        }
    }

    impl Dispatch<ExtDataControlDeviceV1, ()> for State {
        fn event(
            st: &mut Self,
            _: &ExtDataControlDeviceV1,
            ev: ext_data_control_device_v1::Event,
            _: &(),
            _: &Connection,
            _: &QueueHandle<Self>,
        ) {
            if let ext_data_control_device_v1::Event::Finished = ev {
                st.clip.cancelled = true;
            }
        }
        event_created_child!(State, ExtDataControlDeviceV1, [
            ext_data_control_device_v1::EVT_DATA_OFFER_OPCODE => (ExtDataControlOfferV1, ()),
        ]);
    }

    delegate_noop!(State: ignore wl_shm::WlShm);
    delegate_noop!(State: ignore wl_seat::WlSeat);
    delegate_noop!(State: ignore wl_buffer::WlBuffer);
    delegate_noop!(State: ignore wl_surface::WlSurface);
    delegate_noop!(State: ignore ExtDataControlOfferV1);
    delegate_noop!(State: wl_compositor::WlCompositor);
    delegate_noop!(State: wl_shm_pool::WlShmPool);
    delegate_noop!(State: wl_region::WlRegion);
    delegate_noop!(State: ZwlrLayerShellV1);
    delegate_noop!(State: WpViewporter);
    delegate_noop!(State: WpViewport);
    delegate_noop!(State: ZxdgOutputManagerV1);
    delegate_noop!(State: ExtImageCopyCaptureManagerV1);
    delegate_noop!(State: ExtOutputImageCaptureSourceManagerV1);
    delegate_noop!(State: ExtForeignToplevelImageCaptureSourceManagerV1);
    delegate_noop!(State: ExtImageCaptureSourceV1);
    delegate_noop!(State: ZwlrScreencopyManagerV1);
    delegate_noop!(State: ExtDataControlManagerV1);
}

const USAGE: &str = "\
usage: mios-shot [region|window|output|all|probe] [options]
  region                 drag a rectangle on the frozen screen (click = the window under the pointer)
  window                 the active window (--window ID: a Hyprland stableId / foreign-toplevel identifier)
  output                 the focused output (--output NAME)
  all                    every output in one image
  probe                  report the capture protocols, outputs and backend that would be used
options (each overrides its [desktop.screenshot] key):
  --output NAME  --window active|ID  --geometry \"X,Y WxH\"   (logical, global coordinates)
  --delay S  --cursor|--no-cursor  --freeze|--no-freeze  --copy|--no-copy  --save|--no-save
  --file PATH|-  --format png|jpg|webp  --quality 1-100  --edit  --no-notify  --no-flash
  --quiet (no flash, sound or notification: for scripts and agents)
  --selector shell|slurp  --backend auto|ext|wlr|portal  --window-source frozen|toplevel  --json
exit status: 0 done, 1 failed, 2 cancelled (nothing written), 64 usage";

// ---- CLI ---------------------------------------------------------------------------------------------

#[derive(Default, Debug)]
struct Args {
    mode: String,
    output: Option<String>,
    window: Option<String>,
    geometry: Option<Rect>,
    delay: Option<f64>,
    cursor: Option<bool>,
    freeze: Option<bool>,
    copy: Option<bool>,
    save: Option<bool>,
    file: Option<String>,
    format: Option<String>,
    quality: Option<u8>,
    edit: bool,
    notify: Option<bool>,
    flash: Option<bool>,
    quiet: bool,
    selector: Option<String>,
    backend: Option<String>,
    window_source: Option<String>,
    json: bool,
}

fn parse_geometry(s: &str) -> Result<Rect, String> {
    // slurp's "X,Y WxH" (also "X Y W H").
    let n: Vec<f64> = s
        .split(|c: char| c == ',' || c == 'x' || c.is_whitespace())
        .filter(|t| !t.is_empty())
        .map(|t| t.parse::<f64>().map_err(|_| format!("bad geometry {s:?}")))
        .collect::<Result<_, _>>()?;
    match n[..] {
        [x, y, w, h] if w > 0.0 && h > 0.0 => Ok(Rect { x, y, w, h }),
        _ => Err(format!("bad geometry {s:?} (want \"X,Y WxH\")")),
    }
}

fn parse_args(argv: &[String]) -> Result<Args, String> {
    let mut a = Args::default();
    let mut it = argv.iter();
    let val = |it: &mut std::slice::Iter<String>, k: &str| -> Result<String, String> {
        it.next().cloned().ok_or_else(|| format!("{k} needs a value"))
    };
    while let Some(arg) = it.next() {
        let (k, inline) = match arg.split_once('=') {
            Some((k, v)) if k.starts_with("--") => (k.to_string(), Some(v.to_string())),
            _ => (arg.clone(), None),
        };
        let mut v = |name: &str| -> Result<String, String> {
            match &inline {
                Some(x) => Ok(x.clone()),
                None => val(&mut it, name),
            }
        };
        match k.as_str() {
            "region" | "area" | "window" | "active" | "output" | "screen" | "all" | "probe" if a.mode.is_empty() => {
                a.mode = match k.as_str() {
                    "area" => "region",
                    "active" => "window",
                    "screen" => "output",
                    m => m,
                }
                .to_string()
            }
            "--output" => a.output = Some(v("--output")?),
            "--window" => a.window = Some(v("--window")?),
            "--geometry" => a.geometry = Some(parse_geometry(&v("--geometry")?)?),
            "--delay" => a.delay = Some(v("--delay")?.parse().map_err(|_| "--delay needs seconds")?),
            "--cursor" => a.cursor = Some(true),
            "--no-cursor" => a.cursor = Some(false),
            "--freeze" => a.freeze = Some(true),
            "--no-freeze" => a.freeze = Some(false),
            "--copy" => a.copy = Some(true),
            "--no-copy" => a.copy = Some(false),
            "--save" => a.save = Some(true),
            "--no-save" => a.save = Some(false),
            "--file" | "-o" => a.file = Some(v("--file")?),
            "--format" => a.format = Some(v("--format")?),
            "--quality" => {
                let q: u8 = v("--quality")?.parse().map_err(|_| "--quality needs 1-100")?;
                a.quality = Some(q.clamp(1, 100))
            }
            "--edit" => a.edit = true,
            "--notify" => a.notify = Some(true),
            "--no-notify" => a.notify = Some(false),
            "--flash" => a.flash = Some(true),
            "--no-flash" => a.flash = Some(false),
            "--quiet" => a.quiet = true,
            "--selector" => a.selector = Some(v("--selector")?),
            "--backend" => a.backend = Some(v("--backend")?),
            "--window-source" => a.window_source = Some(v("--window-source")?),
            "--json" => a.json = true,
            "-h" | "--help" => return Err(String::new()),
            other => return Err(format!("unknown argument {other:?}")),
        }
    }
    if a.mode.is_empty() {
        a.mode = "region".into();
    }
    Ok(a)
}

// ---- settings ([desktop.screenshot]) -----------------------------------------------------------------

#[derive(Debug, Clone)]
struct Settings {
    directory: String,
    filename: String,
    format: Format,
    quality: u8,
    png_compression: String,
    copy: bool,
    save: bool,
    cursor: bool,
    freeze: bool,
    delay_s: f64,
    selector: String,
    window_source: String,
    scale: String,
    backend: String,
    sound: bool,
    sound_command: Vec<String>,
    flash: bool,
    notify: bool,
    notify_timeout_ms: i32,
    notify_actions: Vec<String>,
    notify_wait_s: u64,
    editors: Vec<Vec<String>>,
    open_command: Vec<String>,
    shell_ipc: Vec<String>,
    slurp: Vec<String>,
    selection_timeout_s: u64,
    selector_connect_ms: u64,
    portal_timeout_s: u64,
}

fn at<'a>(v: &'a toml::Value, path: &str) -> Option<&'a toml::Value> {
    path.split('.').try_fold(v, |v, k| v.get(k))
}

fn strings(v: Option<&toml::Value>) -> Option<Vec<String>> {
    v?.as_array()?.iter().map(|x| x.as_str().map(str::to_string)).collect()
}

impl Settings {
    fn from_table(t: &toml::Value) -> Result<Settings, String> {
        let s = |k: &str, d: &str| t.get(k).and_then(|v| v.as_str()).unwrap_or(d).to_string();
        let b = |k: &str, d: bool| t.get(k).and_then(|v| v.as_bool()).unwrap_or(d);
        let n = |k: &str, d: f64| {
            t.get(k).and_then(|v| v.as_float().or_else(|| v.as_integer().map(|i| i as f64))).unwrap_or(d)
        };
        let list = |k: &str, d: &[&str]| strings(t.get(k)).unwrap_or_else(|| d.iter().map(|x| x.to_string()).collect());
        let editors = t
            .get("editors")
            .and_then(|v| v.as_array())
            .map(|a| a.iter().filter_map(|e| strings(Some(e))).filter(|e| !e.is_empty()).collect())
            .unwrap_or_default();
        Ok(Settings {
            directory: s("directory", ""),
            filename: s("filename", "MiOS-%Y-%m-%d_%H%M%S"),
            format: Format::parse(&s("format", "png"))?,
            quality: n("quality", 90.0).clamp(1.0, 100.0) as u8,
            png_compression: s("png_compression", "fast"),
            copy: b("copy", true),
            save: b("save", true),
            cursor: b("cursor", false),
            freeze: b("freeze", true),
            delay_s: n("delay_s", 0.0).max(0.0),
            selector: s("selector", "shell"),
            window_source: s("window_source", "frozen"),
            scale: s("scale", "max"),
            backend: s("backend", "auto"),
            sound: b("sound", false),
            sound_command: list("sound_command", &[]),
            flash: b("flash", true),
            notify: b("notify", true),
            notify_timeout_ms: n("notify_timeout_ms", 6000.0) as i32,
            notify_actions: list("notify_actions", &["open", "edit", "folder", "copy-path", "delete"]),
            notify_wait_s: n("notify_wait_s", 1800.0).max(1.0) as u64,
            editors,
            open_command: list("open_command", &["xdg-open"]),
            shell_ipc: list(
                "shell_ipc",
                &["quickshell", "ipc", "--path", "/usr/share/mios/quickshell/Config.qml", "--any-display", "call"],
            ),
            slurp: list("slurp", &["slurp", "-d", "-f", "%x %y %w %h"]),
            selection_timeout_s: n("selection_timeout_s", 120.0).max(1.0) as u64,
            selector_connect_ms: n("selector_connect_ms", 3000.0).max(100.0) as u64,
            portal_timeout_s: n("portal_timeout_s", 120.0).max(1.0) as u64,
        })
    }

    fn load() -> Result<Settings, String> {
        let paths = mios_resolver::layers::resolve_layer_paths(None);
        let merged = mios_resolver::layers::merge_layer_files(&paths).map_err(|e| format!("SSOT: {e}"))?;
        let t = at(&merged, "desktop.screenshot").ok_or("missing SSOT table [desktop.screenshot]")?;
        if t.get("enabled").and_then(|v| v.as_bool()) == Some(false) {
            return Err("screenshots are disabled ([desktop.screenshot].enabled = false)".into());
        }
        Settings::from_table(t)
    }

    fn apply(&mut self, a: &Args) -> Result<(), String> {
        if let Some(f) = &a.format {
            self.format = Format::parse(f)?;
        }
        if let Some(q) = a.quality {
            self.quality = q;
        }
        macro_rules! over {
            ($($f:ident),*) => { $( if let Some(v) = a.$f.clone() { self.$f = v; } )* };
        }
        over!(cursor, freeze, copy, save, notify, flash, selector, window_source, backend);
        if a.quiet {
            (self.flash, self.sound, self.notify) = (false, false, false);
        }
        if let Some(d) = a.delay {
            self.delay_s = d.max(0.0);
        }
        Ok(())
    }

    /// Screenshots directory: [desktop.screenshot].directory, else $XDG_SCREENSHOTS_DIR, else
    /// XDG_PICTURES_DIR/Screenshots (GNOME's convention; xdg-user-dirs has no screenshots key).
    fn dir(&self) -> PathBuf {
        let home = std::env::var("HOME").unwrap_or_else(|_| "/".into());
        let expand = |p: &str| -> PathBuf {
            let p = p.replace("$HOME", &home);
            match p.strip_prefix("~/") {
                Some(rest) => Path::new(&home).join(rest),
                None => PathBuf::from(p),
            }
        };
        if !self.directory.is_empty() {
            return expand(&self.directory);
        }
        if let Ok(d) = std::env::var("XDG_SCREENSHOTS_DIR") {
            if !d.is_empty() {
                return expand(&d);
            }
        }
        pictures_dir(&home).join("Screenshots")
    }
}

fn pictures_dir(home: &str) -> PathBuf {
    if let Ok(d) = std::env::var("XDG_PICTURES_DIR") {
        if !d.is_empty() {
            return PathBuf::from(d.replace("$HOME", home));
        }
    }
    let cfg = std::env::var("XDG_CONFIG_HOME").unwrap_or_else(|_| format!("{home}/.config"));
    if let Ok(t) = std::fs::read_to_string(format!("{cfg}/user-dirs.dirs")) {
        for l in t.lines() {
            if let Some(v) = l.trim().strip_prefix("XDG_PICTURES_DIR=") {
                let v = v.trim().trim_matches('"').replace("$HOME", home);
                if !v.is_empty() {
                    return PathBuf::from(v);
                }
            }
        }
    }
    Path::new(home).join("Pictures")
}

fn which(cmd: &str) -> Option<PathBuf> {
    if cmd.contains('/') {
        return Path::new(cmd).is_file().then(|| PathBuf::from(cmd));
    }
    std::env::var_os("PATH")?.to_str()?.split(':').map(|d| Path::new(d).join(cmd)).find(|p| p.is_file())
}

// ---- Hyprland IPC ------------------------------------------------------------------------------------

fn hypr(cmd: &str) -> Option<Json> {
    let sig = std::env::var("HYPRLAND_INSTANCE_SIGNATURE").ok()?;
    let rt = std::env::var("XDG_RUNTIME_DIR").ok()?;
    let mut s = UnixStream::connect(format!("{rt}/hypr/{sig}/.socket.sock")).ok()?;
    s.set_read_timeout(Some(Duration::from_secs(2))).ok()?;
    s.write_all(format!("j/{cmd}").as_bytes()).ok()?;
    let mut out = String::new();
    s.read_to_string(&mut out).ok()?;
    serde_json::from_str(&out).ok()
}

#[derive(Debug, Clone)]
struct Win {
    rect: Rect,
    id: String,
    class: String,
    title: String,
    floating: bool,
    focus: i64,
}

fn win_of(c: &Json) -> Option<Win> {
    let at = c.get("at")?.as_array()?;
    let size = c.get("size")?.as_array()?;
    Some(Win {
        rect: Rect { x: at.first()?.as_f64()?, y: at.get(1)?.as_f64()?, w: size.first()?.as_f64()?, h: size.get(1)?.as_f64()? },
        id: c.get("stableId").and_then(|v| v.as_str().map(str::to_string).or_else(|| Some(v.to_string()))).unwrap_or_default(),
        class: c.get("class").and_then(Json::as_str).unwrap_or("").to_string(),
        title: c.get("title").and_then(Json::as_str).unwrap_or("").to_string(),
        floating: c.get("floating").and_then(Json::as_bool).unwrap_or(false),
        focus: c.get("focusHistoryID").and_then(Json::as_i64).unwrap_or(i64::MAX),
    })
}

/// Windows on the workspaces currently shown (grimblast's filter), floating first, then by focus recency.
fn visible_windows() -> Vec<Win> {
    let mut shown: Vec<i64> = Vec::new();
    if let Some(Json::Array(ms)) = hypr("monitors") {
        for m in &ms {
            for k in ["activeWorkspace", "specialWorkspace"] {
                if let Some(id) = m.get(k).and_then(|w| w.get("id")).and_then(Json::as_i64) {
                    if id != 0 {
                        shown.push(id);
                    }
                }
            }
        }
    }
    let mut out: Vec<Win> = match hypr("clients") {
        Some(Json::Array(cs)) => cs
            .iter()
            .filter(|c| c.get("mapped").and_then(Json::as_bool) != Some(false) && c.get("hidden").and_then(Json::as_bool) != Some(true))
            .filter(|c| c.get("workspace").and_then(|w| w.get("id")).and_then(Json::as_i64).is_some_and(|id| shown.contains(&id)))
            .filter_map(win_of)
            .collect(),
        _ => Vec::new(),
    };
    out.sort_by_key(|w| (!w.floating, w.focus));
    out
}

fn focused_output() -> Option<String> {
    match hypr("monitors")? {
        Json::Array(ms) => ms
            .iter()
            .find(|m| m.get("focused").and_then(Json::as_bool) == Some(true))
            .and_then(|m| m.get("name").and_then(Json::as_str).map(str::to_string)),
        _ => None,
    }
}

fn enforce_permissions() -> bool {
    hypr("getoption ecosystem:enforce_permissions")
        .and_then(|v| v.get("bool").and_then(Json::as_bool).or_else(|| v.get("int").and_then(Json::as_i64).map(|i| i != 0)))
        .unwrap_or(false)
}

// ---- capture flow ------------------------------------------------------------------------------------

/// What was captured, ready for the side effects.
struct Shot {
    canvas: Canvas,
    mode: String,
    output: String,
    app: String,
    backend: String,
    scale: f64,
    action: Option<String>,
}

#[derive(Debug)]
enum Fail {
    Cancelled,
    Error(String),
}

impl From<String> for Fail {
    fn from(e: String) -> Fail {
        Fail::Error(e)
    }
}

fn output_scale(o: &wl::Output, f: &wl::Frame) -> f64 {
    let w = if f.transform % 2 == 1 { f.height } else { f.width };
    if o.w > 0 { w as f64 / o.w as f64 } else { 1.0 }
}

fn pixels(f: &wl::Frame) -> img::Pixels<'_> {
    img::Pixels {
        width: f.width,
        height: f.height,
        stride: f.stride,
        format: u32::from(f.format),
        transform: f.transform,
        y_invert: f.y_invert,
        data: &f.map[..],
    }
}

fn out_rect(o: &wl::Output) -> Rect {
    Rect { x: o.x as f64, y: o.y as f64, w: o.w as f64, h: o.h as f64 }
}

/// Composite `r` from the captured outputs at the configured scale rule.
fn compose(wl: &wl::Wl, idx: &[usize], frames: &[wl::Frame], r: &Rect, rule: &str) -> (Canvas, f64) {
    let mut s: f64 = 0.0;
    let mut placed = Vec::new();
    for (k, &o) in idx.iter().enumerate() {
        let out = &wl.st.outputs[o];
        let rect = out_rect(out);
        if !rect.intersects(r) {
            continue;
        }
        s = s.max(output_scale(out, &frames[k]));
        placed.push(Placed { px: pixels(&frames[k]), x: rect.x, y: rect.y, w: rect.w, h: rect.h, opaque: true });
    }
    if rule == "1" || s <= 0.0 {
        s = 1.0;
    }
    (img::compose(r, s, &placed), s)
}

fn check_denied(frames: &[wl::Frame]) -> Result<(), String> {
    if frames.iter().all(|f| pixels(f).all_black()) && enforce_permissions() {
        let exe = std::env::current_exe().map(|p| p.display().to_string()).unwrap_or_else(|_| "mios-shot".into());
        return Err(format!(
            "Hyprland denied screen capture (ecosystem:enforce_permissions is on and the frame is blank): allow it with \
             hl.permission({{binary = \"{exe}\", type = \"screencopy\", mode = \"allow\"}}) ([desktop.screenshot].hyprland_permission)"
        ));
    }
    Ok(())
}

fn all_outputs(wl: &wl::Wl) -> Vec<usize> {
    (0..wl.st.outputs.len()).filter(|&i| wl.st.outputs[i].w > 0 && wl.st.outputs[i].h > 0).collect()
}

/// Capture the outputs `idx`; returns the ones that answered with their frames. `required`: all of them must
/// (a crop that spans an output which did not render would be wrong); otherwise silent outputs are skipped.
fn capture_outputs(
    wl: &mut wl::Wl,
    idx: &[usize],
    cfg: &Settings,
    b: wl::Backend,
    required: bool,
) -> Result<(Vec<usize>, Vec<wl::Frame>), String> {
    let srcs: Vec<wl::Source> = idx.iter().map(|&i| wl::Source::Output(i)).collect();
    let mut keep = Vec::new();
    let mut frames = Vec::new();
    let mut errs = Vec::new();
    for (k, r) in wl.capture(&srcs, cfg.cursor, b)?.into_iter().enumerate() {
        match r {
            Ok(f) => {
                keep.push(idx[k]);
                frames.push(f);
            }
            Err(e) => errs.push(format!("{}: {e}", wl.st.outputs[idx[k]].name)),
        }
    }
    if frames.is_empty() || (required && !errs.is_empty()) {
        return Err(errs.join("; "));
    }
    for e in &errs {
        eprintln!("mios-shot: skipped {e}");
    }
    check_denied(&frames)?;
    Ok((keep, frames))
}

fn shoot(a: &Args, cfg: &Settings) -> Result<Shot, Fail> {
    if cfg.backend == "portal" {
        return portal_capture(a.mode == "region" && a.geometry.is_none(), cfg.portal_timeout_s).map_err(Fail::from);
    }
    let mut wl = match wl::Wl::connect() {
        Ok(w) => w,
        Err(e) if cfg.backend == "auto" => {
            return portal_capture(a.mode == "region" && a.geometry.is_none(), cfg.portal_timeout_s)
                .map_err(|p| Fail::Error(format!("{e}; portal fallback: {p}")));
        }
        Err(e) => return Err(Fail::Error(e)),
    };
    let Some(b) = wl.backend(&cfg.backend) else {
        if cfg.backend == "auto" {
            return portal_capture(a.mode == "region" && a.geometry.is_none(), cfg.portal_timeout_s).map_err(|p| {
                Fail::Error(format!("the compositor offers neither ext-image-copy-capture nor wlr-screencopy; portal fallback: {p}"))
            });
        }
        return Err(Fail::Error(format!("capture backend {:?} is not available on this compositor", cfg.backend)));
    };
    let outs = all_outputs(&wl);
    if outs.is_empty() {
        return Err(Fail::Error("no outputs to capture".into()));
    }
    let name_of = |wl: &wl::Wl, i: usize| wl.st.outputs[i].name.clone();
    let find_output = |wl: &wl::Wl, name: &str| outs.iter().copied().find(|&i| wl.st.outputs[i].name == name);
    let mut shot = Shot {
        canvas: Canvas { w: 0, h: 0, rgba: Vec::new() },
        mode: a.mode.clone(),
        output: String::new(),
        app: String::new(),
        backend: b.name().into(),
        scale: 1.0,
        action: None,
    };
    match a.mode.as_str() {
        "output" => {
            let want = a.output.clone().or_else(focused_output);
            let i = match &want {
                Some(n) => find_output(&wl, n).ok_or_else(|| Fail::Error(format!("no output named {n}")))?,
                None => outs[0],
            };
            let (_, frames) = capture_outputs(&mut wl, &[i], cfg, b, true)?;
            let r = out_rect(&wl.st.outputs[i]);
            let (c, s) = compose(&wl, &[i], &frames, &r, "max");
            shot.canvas = c;
            shot.scale = s;
            shot.output = name_of(&wl, i);
        }
        "all" => {
            let (got, frames) = capture_outputs(&mut wl, &outs, cfg, b, false)?;
            let r = got.iter().map(|&i| out_rect(&wl.st.outputs[i])).reduce(|a, b| a.union(&b)).unwrap();
            let (c, s) = compose(&wl, &got, &frames, &r, &cfg.scale);
            shot.canvas = c;
            shot.scale = s;
            shot.output = "all".into();
        }
        "window" => {
            let wins = visible_windows();
            let win = match a.window.as_deref() {
                None | Some("active") => hypr("activewindow").as_ref().and_then(win_of),
                Some(id) => wins.iter().find(|w| w.id == id).cloned().or_else(|| {
                    Some(Win { rect: Rect { x: 0.0, y: 0.0, w: 0.0, h: 0.0 }, id: id.into(), class: String::new(), title: String::new(), floating: false, focus: 0 })
                }),
            }
            .filter(|w| !w.id.is_empty())
            .ok_or_else(|| Fail::Error("no active window".into()))?;
            shot.app = win.class.clone();
            window_shot(&mut wl, &outs, &win, cfg, b, &mut shot)?;
        }
        "region" => {
            if let Some(r) = a.geometry {
                let hit: Vec<usize> = outs.iter().copied().filter(|&i| out_rect(&wl.st.outputs[i]).intersects(&r)).collect();
                if hit.is_empty() {
                    return Err(Fail::Error("the geometry is outside every output".into()));
                }
                let (_, frames) = capture_outputs(&mut wl, &hit, cfg, b, true)?;
                let (c, s) = compose(&wl, &hit, &frames, &r, &cfg.scale);
                shot.canvas = c;
                shot.scale = s;
                shot.output = name_of(&wl, hit[0]);
            } else {
                region_shot(&mut wl, &outs, cfg, b, &mut shot)?;
            }
        }
        m => return Err(Fail::Error(format!("unknown mode {m}"))),
    }
    Ok(shot)
}

fn window_shot(wl: &mut wl::Wl, outs: &[usize], win: &Win, cfg: &Settings, b: wl::Backend, shot: &mut Shot) -> Result<(), Fail> {
    let toplevel = cfg.window_source == "toplevel" || win.rect.w <= 0.0;
    if toplevel {
        if !wl.can_capture_toplevels() {
            return Err(Fail::Error("window capture by toplevel needs ext-image-copy-capture with foreign-toplevel sources".into()));
        }
        let frame = wl
            .capture(&[wl::Source::Toplevel(&win.id)], cfg.cursor, wl::Backend::Ext)?
            .pop()
            .ok_or_else(|| Fail::Error("no toplevel frame".into()))??;
        let px = pixels(&frame);
        shot.canvas = img::upright(&px, false);
        shot.backend = format!("{} (toplevel)", wl::Backend::Ext.name());
        shot.output = win.id.clone();
        return Ok(());
    }
    let hit: Vec<usize> = outs.iter().copied().filter(|&i| out_rect(&wl.st.outputs[i]).intersects(&win.rect)).collect();
    if hit.is_empty() {
        return Err(Fail::Error("the window is not on any output".into()));
    }
    let (_, frames) = capture_outputs(wl, &hit, cfg, b, true)?;
    let (c, s) = compose(wl, &hit, &frames, &win.rect, &cfg.scale);
    shot.canvas = c;
    shot.scale = s;
    shot.output = wl.st.outputs[hit[0]].name.clone();
    Ok(())
}

fn region_shot(wl: &mut wl::Wl, outs: &[usize], cfg: &Settings, b: wl::Backend, shot: &mut Shot) -> Result<(), Fail> {
    let wins = visible_windows();
    // Freeze: capture every output now; the frames are both the frozen frame and the source of the crop, so the
    // result is exactly what the user saw while selecting.
    let frozen = if cfg.freeze { Some(capture_outputs(wl, outs, cfg, b, false)?) } else { None };
    // Outputs that did not render are left out of the freeze and the selection.
    let outs: Vec<usize> = match &frozen {
        Some((got, f)) => {
            if wl.can_freeze() {
                wl.freeze(got, f)?;
            }
            got.clone()
        }
        None => outs.to_vec(),
    };
    let outs = &outs[..];
    let sel = select(wl, outs, &wins, cfg);
    wl.unfreeze();
    let sel = sel?;
    shot.action = sel.action.clone();
    let rect = match &sel.kind {
        Pick::Region(r) => *r,
        Pick::Output(n) => outs
            .iter()
            .map(|&i| &wl.st.outputs[i])
            .find(|o| &o.name == n)
            .map(out_rect)
            .ok_or_else(|| Fail::Error(format!("selector picked unknown output {n}")))?,
        Pick::Window(id) => {
            let w = wins.iter().find(|w| &w.id == id).cloned().ok_or_else(|| Fail::Error(format!("selector picked unknown window {id}")))?;
            shot.app = w.class.clone();
            if cfg.window_source == "toplevel" {
                shot.mode = "window".into();
                return window_shot(wl, outs, &w, cfg, b, shot);
            }
            w.rect
        }
    };
    let hit: Vec<usize> = outs.iter().copied().filter(|&i| out_rect(&wl.st.outputs[i]).intersects(&rect)).collect();
    if hit.is_empty() {
        return Err(Fail::Error("the selection is outside every output".into()));
    }
    let (c, s) = match &frozen {
        Some((got, f)) => compose(wl, got, f, &rect, &cfg.scale),
        None => {
            let (_, frames) = capture_outputs(wl, &hit, cfg, b, true)?;
            compose(wl, &hit, &frames, &rect, &cfg.scale)
        }
    };
    shot.canvas = c;
    shot.scale = s;
    shot.output = wl.st.outputs[hit[0]].name.clone();
    Ok(())
}

// ---- selector ----------------------------------------------------------------------------------------

#[derive(Debug, PartialEq)]
enum Pick {
    Region(Rect),
    Window(String),
    Output(String),
}

#[derive(Debug)]
struct Selection {
    kind: Pick,
    action: Option<String>,
}

fn parse_selection(line: &str) -> Result<Selection, Fail> {
    let v: Json = serde_json::from_str(line.trim()).map_err(|e| Fail::Error(format!("selector sent bad JSON: {e}")))?;
    if v.get("cancel").and_then(Json::as_bool) == Some(true) {
        return Err(Fail::Cancelled);
    }
    let action = v.get("action").and_then(Json::as_str).map(str::to_string);
    if let Some(a) = &action {
        if !matches!(a.as_str(), "copy" | "save" | "copysave" | "edit") {
            return Err(Fail::Error(format!("selector sent unknown action {a:?}")));
        }
    }
    let kind = match v.get("kind").and_then(Json::as_str) {
        Some("region") => {
            let r: Vec<f64> = v.get("rect").and_then(Json::as_array).map(|a| a.iter().filter_map(Json::as_f64).collect()).unwrap_or_default();
            match r[..] {
                [x, y, w, h] if w >= 1.0 && h >= 1.0 => Pick::Region(Rect { x, y, w, h }),
                _ => return Err(Fail::Error("selector sent an empty region".into())),
            }
        }
        Some("window") => Pick::Window(v.get("id").and_then(Json::as_str).unwrap_or("").to_string()),
        Some("output") => Pick::Output(v.get("name").and_then(Json::as_str).unwrap_or("").to_string()),
        _ => return Err(Fail::Error("selector sent no kind".into())),
    };
    Ok(Selection { kind, action })
}

fn session_json(wl: &wl::Wl, outs: &[usize], wins: &[Win], cfg: &Settings) -> Json {
    let cursor = hypr("cursorpos").map(|c| json!([c.get("x"), c.get("y")])).unwrap_or(Json::Null);
    json!({
        "v": 1,
        "mode": "region",
        "focused": focused_output(),
        "cursor": cursor,
        "outputs": outs.iter().map(|&i| { let o = &wl.st.outputs[i]; json!({"name": o.name, "x": o.x, "y": o.y, "w": o.w, "h": o.h}) }).collect::<Vec<_>>(),
        "windows": wins.iter().map(|w| json!({"id": w.id, "x": w.rect.x, "y": w.rect.y, "w": w.rect.w, "h": w.rect.h, "class": w.class, "title": w.title})).collect::<Vec<_>>(),
        "copy": cfg.copy,
        "save": cfg.save,
        "editor": cfg.editors.iter().any(|e| which(&e[0]).is_some()),
    })
}

fn select(wl: &mut wl::Wl, outs: &[usize], wins: &[Win], cfg: &Settings) -> Result<Selection, Fail> {
    if cfg.selector != "slurp" {
        match select_shell(wl, outs, wins, cfg) {
            Err(Fail::Error(e)) if e.starts_with("shell selector unavailable") => eprintln!("mios-shot: {e}; using slurp"),
            other => return other,
        }
    }
    select_slurp(wins, cfg)
}

fn runtime_dir() -> PathBuf {
    let rt = std::env::var("XDG_RUNTIME_DIR").map(PathBuf::from).unwrap_or_else(|_| std::env::temp_dir());
    rt.join("mios")
}

fn poll2(a: i32, b: i32, ms: i32) -> (bool, bool) {
    let mut p = [libc::pollfd { fd: a, events: libc::POLLIN, revents: 0 }, libc::pollfd { fd: b, events: libc::POLLIN, revents: 0 }];
    // SAFETY: two valid pollfds for the duration of the call.
    let n = unsafe { libc::poll(p.as_mut_ptr(), 2, ms) };
    if n <= 0 {
        return (false, false);
    }
    (p[0].revents != 0, p[1].revents != 0)
}

/// The glass selector in mios-shell (Shot.qml): a private socket, one session line out, one result line back.
fn select_shell(wl: &mut wl::Wl, outs: &[usize], wins: &[Win], cfg: &Settings) -> Result<Selection, Fail> {
    let dir = runtime_dir();
    std::fs::create_dir_all(&dir).map_err(|e| Fail::Error(format!("shell selector unavailable: {e}")))?;
    let _ = std::fs::set_permissions(&dir, std::os::unix::fs::PermissionsExt::from_mode(0o700));
    let sock = dir.join(format!("shot-{}.sock", std::process::id()));
    let _ = std::fs::remove_file(&sock);
    let listener = UnixListener::bind(&sock).map_err(|e| Fail::Error(format!("shell selector unavailable: {e}")))?;
    let _ = std::fs::set_permissions(&sock, std::os::unix::fs::PermissionsExt::from_mode(0o600));
    struct Rm(PathBuf);
    impl Drop for Rm {
        fn drop(&mut self) {
            let _ = std::fs::remove_file(&self.0);
        }
    }
    let _rm = Rm(sock.clone());
    let (prog, rest) = cfg.shell_ipc.split_first().ok_or_else(|| Fail::Error("shell selector unavailable: empty shell_ipc".into()))?;
    let st = Command::new(prog)
        .args(rest)
        .args(["shot", "select", &sock.display().to_string()])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map_err(|e| Fail::Error(format!("shell selector unavailable: {prog}: {e}")))?;
    if !st.success() {
        return Err(Fail::Error(format!("shell selector unavailable: {prog} exited with {st}")));
    }
    listener.set_nonblocking(true).ok();
    let wfd = wl.display_fd();
    let start = Instant::now();
    let mut stream = loop {
        match listener.accept() {
            Ok((s, _)) => break s,
            Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {}
            Err(e) => return Err(Fail::Error(format!("shell selector unavailable: {e}"))),
        }
        if start.elapsed() > Duration::from_millis(cfg.selector_connect_ms) {
            return Err(Fail::Error("shell selector unavailable: mios-shell did not connect".into()));
        }
        wl.flush();
        let (_, w) = poll2(listener.as_raw_fd(), wfd, 50);
        if w {
            wl.dispatch_timeout(0)?;
        }
    };
    stream.set_nonblocking(false).ok();
    let line = format!("{}\n", session_json(wl, outs, wins, cfg));
    stream.write_all(line.as_bytes()).map_err(|e| Fail::Error(format!("selector socket: {e}")))?;
    stream.set_nonblocking(true).ok();
    let mut buf = Vec::new();
    let deadline = Instant::now() + Duration::from_secs(cfg.selection_timeout_s);
    loop {
        let mut chunk = [0u8; 4096];
        match stream.read(&mut chunk) {
            Ok(0) => return Err(Fail::Cancelled),
            Ok(n) => {
                buf.extend_from_slice(&chunk[..n]);
                if let Some(p) = buf.iter().position(|&c| c == b'\n') {
                    return parse_selection(&String::from_utf8_lossy(&buf[..p]));
                }
            }
            Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {}
            Err(e) => return Err(Fail::Error(format!("selector socket: {e}"))),
        }
        if Instant::now() > deadline {
            let _ = stream.write_all(b"{\"cancel\":true}\n");
            return Err(Fail::Cancelled);
        }
        wl.flush();
        let (_, w) = poll2(stream.as_raw_fd(), wfd, 200);
        if w {
            wl.dispatch_timeout(0)?;
        }
    }
}

fn select_slurp(wins: &[Win], cfg: &Settings) -> Result<Selection, Fail> {
    let (prog, rest) = cfg.slurp.split_first().ok_or_else(|| Fail::Error("no selector: shell unavailable and slurp unset".into()))?;
    let mut child = Command::new(prog)
        .args(rest)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| Fail::Error(format!("no selector: {prog}: {e}")))?;
    if let Some(mut si) = child.stdin.take() {
        for w in wins {
            let _ = writeln!(si, "{},{} {}x{}", w.rect.x, w.rect.y, w.rect.w, w.rect.h);
        }
    }
    let out = child.wait_with_output().map_err(|e| Fail::Error(format!("slurp: {e}")))?;
    if !out.status.success() {
        return Err(Fail::Cancelled);
    }
    let r = parse_geometry(String::from_utf8_lossy(&out.stdout).trim()).map_err(|_| Fail::Cancelled)?;
    Ok(Selection { kind: Pick::Region(r), action: None })
}

// ---- output side effects -----------------------------------------------------------------------------

fn spawn_detached(argv: &[String], stdin: Option<OwnedFd>) -> Result<std::process::Child, String> {
    let (prog, rest) = argv.split_first().ok_or("empty command")?;
    let mut c = Command::new(prog);
    c.args(rest).stdout(Stdio::null()).stderr(Stdio::null());
    match stdin {
        Some(fd) => c.stdin(Stdio::from(fd)),
        None => c.stdin(Stdio::null()),
    };
    // SAFETY: setsid is async-signal-safe; the child leaves our session so it outlives a closed terminal.
    unsafe {
        c.pre_exec(|| {
            libc::setsid();
            Ok(())
        });
    }
    c.spawn().map_err(|e| format!("{prog}: {e}"))
}

fn memfd_with(bytes: &[u8]) -> Result<OwnedFd, String> {
    let fd = rustix::fs::memfd_create("mios-shot-data", rustix::fs::MemfdFlags::empty()).map_err(|e| format!("memfd: {e}"))?;
    let mut f = std::fs::File::from(fd);
    f.write_all(bytes).map_err(|e| format!("memfd: {e}"))?;
    use std::io::Seek;
    f.seek(std::io::SeekFrom::Start(0)).map_err(|e| format!("memfd: {e}"))?;
    Ok(OwnedFd::from(f))
}

/// Frame (mime, bytes) offers for the clipboard resident: "<mime>\n<len>\n<bytes>" each.
fn frame_offers(offers: &[(&str, &[u8])]) -> Vec<u8> {
    let mut v = Vec::new();
    for (m, b) in offers {
        v.extend_from_slice(format!("{m}\n{}\n", b.len()).as_bytes());
        v.extend_from_slice(b);
    }
    v
}

fn unframe_offers(mut d: &[u8]) -> Result<Vec<(String, Vec<u8>)>, String> {
    let mut out = Vec::new();
    while !d.is_empty() {
        let nl = d.iter().position(|&c| c == b'\n').ok_or("bad clipboard framing")?;
        let mime = String::from_utf8_lossy(&d[..nl]).to_string();
        d = &d[nl + 1..];
        let nl = d.iter().position(|&c| c == b'\n').ok_or("bad clipboard framing")?;
        let len: usize = String::from_utf8_lossy(&d[..nl]).parse().map_err(|_| "bad clipboard framing")?;
        d = &d[nl + 1..];
        if d.len() < len {
            return Err("short clipboard data".into());
        }
        out.push((mime, d[..len].to_vec()));
        d = &d[len..];
    }
    Ok(out)
}

/// Hand `offers` to a detached clipboard resident (this binary, `__clip`), which owns the selection until
/// another client replaces it. Falls back to wl-copy (image types only).
fn copy_to_clipboard(offers: &[(&str, &[u8])]) -> Result<String, String> {
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    let data = frame_offers(offers);
    let (r, w) = rustix::pipe::pipe().map_err(|e| format!("pipe: {e}"))?;
    let mut c = Command::new(&exe);
    c.arg("__clip").stdin(Stdio::from(memfd_with(&data)?)).stdout(Stdio::from(w)).stderr(Stdio::null());
    // SAFETY: setsid is async-signal-safe.
    unsafe {
        c.pre_exec(|| {
            libc::setsid();
            Ok(())
        });
    }
    let native = c.spawn().map_err(|e| e.to_string()).and_then(|_child| {
        drop(c);
        let mut f = std::fs::File::from(r);
        let fd = f.as_raw_fd();
        let mut p = libc::pollfd { fd, events: libc::POLLIN, revents: 0 };
        // SAFETY: one valid pollfd.
        if unsafe { libc::poll(&mut p, 1, 3000) } <= 0 {
            return Err("clipboard resident did not answer".to_string());
        }
        let mut line = String::new();
        BufReader::new(&mut f).read_line(&mut line).map_err(|e| e.to_string())?;
        if line.trim() == "ready" {
            Ok("native".to_string())
        } else {
            Err(format!("clipboard resident: {}", line.trim()))
        }
    });
    match native {
        Ok(s) => Ok(s),
        Err(e) => {
            let (mime, bytes) = offers.first().ok_or("nothing to copy")?;
            let mut child = Command::new("wl-copy")
                .args(["--type", mime])
                .stdin(Stdio::piped())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .spawn()
                .map_err(|x| format!("{e}; wl-copy: {x}"))?;
            child.stdin.take().ok_or("wl-copy stdin")?.write_all(bytes).map_err(|x| format!("wl-copy: {x}"))?;
            let st = child.wait().map_err(|x| format!("wl-copy: {x}"))?;
            if st.success() { Ok(format!("wl-copy ({e})")) } else { Err(format!("{e}; wl-copy exited {st}")) }
        }
    }
}

fn shell_call(cfg: &Settings, args: &[&str], wait: bool) -> bool {
    let Some((prog, rest)) = cfg.shell_ipc.split_first() else { return false };
    let mut c = Command::new(prog);
    c.args(rest).args(args).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    if wait {
        c.status().map(|s| s.success()).unwrap_or(false)
    } else {
        c.spawn().is_ok()
    }
}

fn editor_argv(cfg: &Settings, file: &Path, out: &Path) -> Option<Vec<String>> {
    let e = cfg.editors.iter().find(|e| which(&e[0]).is_some())?;
    Some(
        e.iter()
            .map(|a| a.replace("{file}", &file.display().to_string()).replace("{out}", &out.display().to_string()))
            .collect(),
    )
}

type Hints<'a> = HashMap<&'a str, zbus::zvariant::Value<'a>>;

fn notify_dbus(summary: &str, body: &str, actions: &[String], thumb: Option<&Canvas>, timeout: i32) -> Result<u32, String> {
    let conn = zbus::blocking::Connection::session().map_err(|e| e.to_string())?;
    let p = zbus::blocking::Proxy::new(&conn, "org.freedesktop.Notifications", "/org/freedesktop/Notifications", "org.freedesktop.Notifications")
        .map_err(|e| e.to_string())?;
    let mut hints: Hints = HashMap::new();
    hints.insert("desktop-entry", zbus::zvariant::Value::from("mios-shot"));
    hints.insert("category", zbus::zvariant::Value::from("transfer.complete"));
    if let Some(t) = thumb {
        let s = zbus::zvariant::Structure::from((t.w as i32, t.h as i32, (t.w * 4) as i32, true, 8i32, 4i32, t.rgba.clone()));
        hints.insert("image-data", zbus::zvariant::Value::from(s));
    }
    let id: u32 = p
        .call("Notify", &("MiOS Screenshot", 0u32, "applets-screenshooter", summary, body, actions.to_vec(), hints, timeout))
        .map_err(|e| e.to_string())?;
    Ok(id)
}

fn action_label(k: &str) -> &'static str {
    match k {
        "default" => "Open",
        "open" => "Open",
        "edit" => "Edit",
        "folder" => "Show in folder",
        "copy-path" => "Copy path",
        "delete" => "Delete",
        "save" => "Save",
        _ => "",
    }
}

/// Post the result notification; returns how it was shown ("server", "toast", "off" or "failed: ...").
fn notify(cfg: &Settings, shot: &Shot, file: Option<&Path>, copied: bool, data: Option<&[u8]>) -> String {
    if !cfg.notify {
        return "off".into();
    }
    let summary = match (file.is_some(), copied) {
        (true, true) => "Screenshot saved and copied",
        (true, false) => "Screenshot saved",
        (false, true) => "Screenshot copied to the clipboard",
        (false, false) => "Screenshot taken",
    };
    let body = match file {
        Some(f) => format!("{} ({}x{})", f.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default(), shot.canvas.w, shot.canvas.h),
        None => format!("{}x{}", shot.canvas.w, shot.canvas.h),
    };
    let have_editor = cfg.editors.iter().any(|e| which(&e[0]).is_some());
    let mut keys: Vec<&str> = Vec::new();
    for k in &cfg.notify_actions {
        let ok = match k.as_str() {
            "open" | "folder" | "copy-path" | "delete" => file.is_some(),
            "edit" => have_editor,
            "save" => file.is_none() && data.is_some(),
            _ => false,
        };
        if ok {
            keys.push(k);
        }
    }
    if file.is_some() && keys.contains(&"open") {
        keys.insert(0, "default");
    }
    let actions: Vec<String> = keys.iter().flat_map(|k| [k.to_string(), action_label(k).to_string()]).collect();
    let thumb = img::thumbnail(&shot.canvas, 256);
    match notify_dbus(summary, &body, &actions, Some(&thumb), cfg.notify_timeout_ms) {
        Ok(id) => {
            if !keys.is_empty() {
                let exe = std::env::current_exe().map(|p| p.display().to_string()).unwrap_or_else(|_| "mios-shot".into());
                let mut argv = vec![exe, "__notify-wait".into(), "--id".into(), id.to_string(), "--format".into(), shot_format_ext(cfg).into()];
                if let Some(f) = file {
                    argv.extend(["--file".into(), f.display().to_string()]);
                }
                let stdin = match (file, data) {
                    (None, Some(d)) => memfd_with(d).ok(),
                    _ => None,
                };
                if let Err(e) = spawn_detached(&argv, stdin) {
                    eprintln!("mios-shot: notification actions unavailable: {e}");
                }
            }
            "server".into()
        }
        Err(e) => {
            if shell_call(cfg, &["shot", "toast", summary, &body], true) {
                "toast".into()
            } else {
                format!("failed: {e}")
            }
        }
    }
}

fn shot_format_ext(cfg: &Settings) -> &'static str {
    cfg.format.ext()
}

fn effects(cfg: &Settings, shot: &Shot) {
    if cfg.flash {
        let target = if shot.mode == "all" { String::new() } else { shot.output.clone() };
        shell_call(cfg, &["shot", "flash", &target], false);
    }
    if cfg.sound && !cfg.sound_command.is_empty() {
        let _ = spawn_detached(&cfg.sound_command, None);
    }
}

/// Save / copy / notify / edit; returns the JSON result.
fn deliver(a: &Args, cfg: &Settings, shot: Shot) -> Result<Json, String> {
    let (mut copy, mut save, mut edit) = (cfg.copy, cfg.save, a.edit);
    match shot.action.as_deref() {
        Some("copy") => (copy, save) = (true, false),
        Some("save") => (copy, save) = (false, true),
        Some("copysave") => (copy, save) = (true, true),
        Some("edit") => edit = true,
        _ => {}
    }
    effects(cfg, &shot);
    let bytes = img::encode(&shot.canvas, cfg.format, cfg.quality, &cfg.png_compression)?;
    let mut file: Option<PathBuf> = None;
    let mut stdout = false;
    match a.file.as_deref() {
        Some("-") => {
            std::io::stdout().write_all(&bytes).map_err(|e| format!("stdout: {e}"))?;
            stdout = true;
        }
        Some(p) => {
            img::write_replace(Path::new(p), &bytes)?;
            file = Some(PathBuf::from(p));
        }
        None if save => {
            let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0);
            let base = img::render_name(&cfg.filename, now, &shot.mode, &shot.output, &shot.app);
            file = Some(img::save_atomic(&cfg.dir(), &base, cfg.format.ext(), &bytes)?);
        }
        None => {}
    }
    // The clipboard always gets PNG: every app pastes image/png, few take jpeg/webp.
    let mut copied = false;
    let mut clip_via = String::new();
    if copy && !stdout {
        let png;
        let clip: &[u8] = if cfg.format == Format::Png {
            &bytes
        } else {
            png = img::encode(&shot.canvas, Format::Png, 90, "fast")?;
            &png
        };
        match copy_to_clipboard(&[("image/png", clip)]) {
            Ok(v) => {
                copied = true;
                clip_via = v;
            }
            Err(e) => eprintln!("mios-shot: clipboard: {e}"),
        }
    }
    let mut editor = Json::Null;
    if edit {
        let (input, out) = match &file {
            Some(f) => (f.clone(), f.clone()),
            None => {
                let dir = runtime_dir();
                let _ = std::fs::create_dir_all(&dir);
                let tmp = dir.join(format!("shot-edit-{}.{}", std::process::id(), cfg.format.ext()));
                img::write_replace(&tmp, &bytes)?;
                // Unsaved pixels stay in the runtime dir (tmpfs) only until the editor has loaded them.
                let _ = spawn_detached(&["sh".into(), "-c".into(), "sleep 60; rm -f -- \"$0\"".into(), tmp.display().to_string()], None);
                let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0);
                let base = img::render_name(&cfg.filename, now, &shot.mode, &shot.output, &shot.app);
                (tmp, cfg.dir().join(format!("{base}.{}", cfg.format.ext())))
            }
        };
        match editor_argv(cfg, &input, &out) {
            Some(argv) => match spawn_detached(&argv, None) {
                Ok(_) => editor = json!(argv[0]),
                Err(e) => eprintln!("mios-shot: editor: {e}"),
            },
            None => eprintln!("mios-shot: no annotation editor installed ([desktop.screenshot].editors)"),
        }
    }
    let notified = if stdout { "off".to_string() } else { notify(cfg, &shot, file.as_deref(), copied, Some(&bytes)) };
    Ok(json!({
        "ok": true,
        "mode": shot.mode,
        "backend": shot.backend,
        "output": shot.output,
        "width": shot.canvas.w,
        "height": shot.canvas.h,
        "scale": shot.scale,
        "format": cfg.format.ext(),
        "file": file.map(|f| f.display().to_string()),
        "copied": copied,
        "clipboard": clip_via,
        "notify": notified,
        "editor": editor,
    }))
}

// ---- portal ------------------------------------------------------------------------------------------

/// org.freedesktop.portal.Screenshot (GNOME/KDE sessions, or a compositor without capture protocols).
fn portal_capture(interactive: bool, timeout_s: u64) -> Result<Shot, String> {
    use zbus::zvariant::{OwnedValue, Value};
    let conn = zbus::blocking::Connection::session().map_err(|e| format!("portal: {e}"))?;
    let unique = conn.unique_name().ok_or("portal: no bus name")?.trim_start_matches(':').replace('.', "_");
    let token = format!("mios_shot_{}", std::process::id());
    let path = format!("/org/freedesktop/portal/desktop/request/{unique}/{token}");
    let req = zbus::blocking::Proxy::new(&conn, "org.freedesktop.portal.Desktop", path.as_str(), "org.freedesktop.portal.Request")
        .map_err(|e| format!("portal: {e}"))?;
    let mut responses = req.receive_signal("Response").map_err(|e| format!("portal: {e}"))?;
    let portal = zbus::blocking::Proxy::new(&conn, "org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop", "org.freedesktop.portal.Screenshot")
        .map_err(|e| format!("portal: {e}"))?;
    let mut opts: HashMap<&str, Value> = HashMap::new();
    opts.insert("handle_token", Value::from(token.as_str()));
    opts.insert("interactive", Value::from(interactive));
    let _handle: zbus::zvariant::OwnedObjectPath = portal.call("Screenshot", &("", opts)).map_err(|e| format!("portal: {e}"))?;
    let (tx, rx) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        if let Some(m) = responses.next() {
            let _ = tx.send(m.body().deserialize::<(u32, HashMap<String, OwnedValue>)>().map_err(|e| e.to_string()));
        }
    });
    // The portal may ask the user first (xdg-desktop-portal's screenshot permission dialog for host apps): wait
    // long enough for a person, and close the request (and its dialog) when nobody answers.
    let (code, results) = match rx.recv_timeout(Duration::from_secs(if interactive { timeout_s.max(300) } else { timeout_s })) {
        Ok(r) => r.map_err(|e| format!("portal: {e}"))?,
        Err(_) => {
            let _ = req.call_method("Close", &());
            return Err("portal: no response (request closed)".into());
        }
    };
    match code {
        0 => {}
        1 => return Err("portal: the screenshot was cancelled".into()),
        c => return Err(format!("portal: the screenshot was denied (response {c})")),
    }
    let uri: String = results
        .get("uri")
        .and_then(|v| String::try_from(v.clone()).ok())
        .ok_or("portal: response carries no uri")?;
    let p = uri.strip_prefix("file://").ok_or_else(|| format!("portal: unsupported uri {uri}"))?;
    let p = percent_decode(p);
    let img = image::open(&p).map_err(|e| format!("portal: cannot read {p}: {e}"))?.to_rgba8();
    // The portal's temp file is screen content: remove it when it is ours to remove (runtime dir, e.g. XDPH's).
    if let Ok(rt) = std::env::var("XDG_RUNTIME_DIR") {
        if p.starts_with(&rt) {
            let _ = std::fs::remove_file(&p);
        }
    }
    let (w, h) = img.dimensions();
    Ok(Shot {
        canvas: Canvas { w, h, rgba: img.into_raw() },
        mode: if interactive { "region".into() } else { "all".into() },
        output: "portal".into(),
        app: String::new(),
        backend: "org.freedesktop.portal.Screenshot".into(),
        scale: 1.0,
        action: None,
    })
}

fn percent_decode(s: &str) -> String {
    let b = s.as_bytes();
    let mut out = Vec::with_capacity(b.len());
    let mut i = 0;
    while i < b.len() {
        if b[i] == b'%' && i + 2 < b.len() {
            if let Some(v) = std::str::from_utf8(&b[i + 1..i + 3]).ok().and_then(|h| u8::from_str_radix(h, 16).ok()) {
                out.push(v);
                i += 3;
                continue;
            }
        }
        out.push(b[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

// ---- probe -------------------------------------------------------------------------------------------

fn bus_has(name: &str) -> bool {
    zbus::blocking::Connection::session()
        .ok()
        .and_then(|c| zbus::blocking::fdo::DBusProxy::new(&c).ok().and_then(|p| p.name_has_owner(name.try_into().ok()?).ok()))
        .unwrap_or(false)
}

fn probe(cfg: &Settings) -> Json {
    let wl = wl::Wl::connect();
    let mut j = json!({
        "portal": bus_has("org.freedesktop.portal.Desktop"),
        "notification_server": bus_has("org.freedesktop.Notifications"),
        "editor": cfg.editors.iter().find(|e| which(&e[0]).is_some()).map(|e| e[0].clone()),
        "directory": cfg.dir().display().to_string(),
        "format": cfg.format.ext(),
    });
    match wl {
        Ok(wl) => {
            let wanted = [
                "ext_image_copy_capture_manager_v1",
                "ext_output_image_capture_source_manager_v1",
                "ext_foreign_toplevel_image_capture_source_manager_v1",
                "ext_foreign_toplevel_list_v1",
                "zwlr_screencopy_manager_v1",
                "zwlr_layer_shell_v1",
                "wp_viewporter",
                "ext_data_control_manager_v1",
                "zxdg_output_manager_v1",
            ];
            j["wayland"] = json!(true);
            j["globals"] = json!(wanted.iter().map(|n| (n.to_string(), json!(wl.st.globals.iter().find(|(g, _)| g == n).map(|(_, v)| *v)))).collect::<serde_json::Map<_, _>>());
            j["backend"] = json!(wl.backend(&cfg.backend).map(|b| b.name()).unwrap_or(if cfg.backend == "auto" { "org.freedesktop.portal.Screenshot" } else { "none" }));
            j["toplevel_capture"] = json!(wl.can_capture_toplevels());
            j["freeze"] = json!(wl.can_freeze());
            j["outputs"] = json!(wl.st.outputs.iter().map(|o| json!({"name": o.name, "x": o.x, "y": o.y, "w": o.w, "h": o.h, "mode": [o.mode_w, o.mode_h], "transform": o.transform, "scale": o.scale_int})).collect::<Vec<_>>());
        }
        Err(e) => {
            j["wayland"] = json!(false);
            j["error"] = json!(e);
            j["backend"] = json!(if cfg.backend == "auto" || cfg.backend == "portal" { "org.freedesktop.portal.Screenshot" } else { "none" });
        }
    }
    j
}

// ---- residents ---------------------------------------------------------------------------------------

/// `__clip`: own the clipboard with the framed offers on stdin until replaced. Prints "ready" (or an error)
/// on stdout once, then lets go of stdout so callers piping mios-shot never wait on it.
fn resident_clip() -> i32 {
    let mut data = Vec::new();
    if let Err(e) = std::io::stdin().read_to_end(&mut data) {
        println!("error: stdin: {e}");
        return 1;
    }
    let offers = match unframe_offers(&data) {
        Ok(o) => o,
        Err(e) => {
            println!("error: {e}");
            return 1;
        }
    };
    drop(data);
    let mut wl = match wl::Wl::connect() {
        Ok(w) => w,
        Err(e) => {
            println!("error: {e}");
            return 1;
        }
    };
    let offers: Vec<(String, std::sync::Arc<Vec<u8>>)> = offers.into_iter().map(|(m, b)| (m, std::sync::Arc::new(b))).collect();
    let r = wl.serve_clipboard(offers, || {
        let mut so = std::io::stdout();
        let _ = so.write_all(b"ready\n");
        let _ = so.flush();
        // SAFETY: replace fd 1 with /dev/null so the caller's pipe closes.
        unsafe {
            let n = libc::open(c"/dev/null".as_ptr(), libc::O_WRONLY);
            if n >= 0 {
                libc::dup2(n, 1);
                libc::close(n);
            }
        }
    });
    match r {
        Ok(_) => 0,
        Err(e) => {
            eprintln!("mios-shot __clip: {e}");
            1
        }
    }
}

/// `__notify-wait`: run the notification's actions (open, edit, folder, copy-path, delete, save) until it closes.
fn resident_notify(argv: &[String]) -> i32 {
    let mut id = 0u32;
    let mut file: Option<PathBuf> = None;
    let mut ext = "png".to_string();
    let mut it = argv.iter();
    while let Some(k) = it.next() {
        match k.as_str() {
            "--id" => id = it.next().and_then(|v| v.parse().ok()).unwrap_or(0),
            "--file" => file = it.next().map(PathBuf::from),
            "--format" => ext = it.next().cloned().unwrap_or(ext),
            _ => {}
        }
    }
    let cfg = match Settings::load() {
        Ok(c) => c,
        Err(_) => return 1,
    };
    let mut data = Vec::new();
    if file.is_none() {
        let _ = std::io::stdin().read_to_end(&mut data);
    }
    let Ok(conn) = zbus::blocking::Connection::session() else { return 1 };
    let Ok(p) = zbus::blocking::Proxy::new(&conn, "org.freedesktop.Notifications", "/org/freedesktop/Notifications", "org.freedesktop.Notifications") else {
        return 1;
    };
    let (tx, rx) = std::sync::mpsc::channel::<(String, u32, String)>();
    for sig in ["ActionInvoked", "NotificationClosed"] {
        let Ok(stream) = p.receive_signal(sig) else { return 1 };
        let tx = tx.clone();
        std::thread::spawn(move || {
            for m in stream {
                let r = if sig == "ActionInvoked" {
                    m.body().deserialize::<(u32, String)>().map(|(i, k)| (sig.to_string(), i, k))
                } else {
                    m.body().deserialize::<(u32, u32)>().map(|(i, _)| (sig.to_string(), i, String::new()))
                };
                if let Ok(x) = r {
                    if tx.send(x).is_err() {
                        break;
                    }
                }
            }
        });
    }
    let deadline = Instant::now() + Duration::from_secs(cfg.notify_wait_s);
    while let Some(left) = deadline.checked_duration_since(Instant::now()) {
        let Ok((sig, nid, key)) = rx.recv_timeout(left) else { break };
        if nid != id {
            continue;
        }
        if sig == "NotificationClosed" {
            break;
        }
        run_action(&cfg, &key, file.as_deref(), &data, &ext);
        if key != "copy-path" {
            break;
        }
    }
    0
}

fn run_action(cfg: &Settings, key: &str, file: Option<&Path>, data: &[u8], ext: &str) {
    let fs = |p: &Path| p.display().to_string();
    match (key, file) {
        ("default" | "open", Some(f)) => {
            let mut argv = cfg.open_command.clone();
            argv.push(fs(f));
            let _ = spawn_detached(&argv, None);
        }
        ("folder", Some(f)) => {
            let uri = format!("file://{}", fs(f));
            let shown = zbus::blocking::Connection::session().ok().and_then(|c| {
                c.call_method(Some("org.freedesktop.FileManager1"), "/org/freedesktop/FileManager1", Some("org.freedesktop.FileManager1"), "ShowItems", &(vec![uri.as_str()], ""))
                    .ok()
            });
            if shown.is_none() {
                let mut argv = cfg.open_command.clone();
                argv.push(f.parent().map(fs).unwrap_or_default());
                let _ = spawn_detached(&argv, None);
            }
        }
        ("copy-path", Some(f)) => {
            let p = fs(f);
            let _ = copy_to_clipboard(&[("text/plain;charset=utf-8", p.as_bytes()), ("text/plain", p.as_bytes()), ("UTF8_STRING", p.as_bytes())]);
        }
        ("delete", Some(f)) => {
            // Only the file this run wrote, inside the screenshots directory.
            if f.parent() == Some(cfg.dir().as_path()) || f.starts_with(cfg.dir()) {
                let _ = std::fs::remove_file(f);
            }
        }
        ("edit", Some(f)) => {
            if let Some(argv) = editor_argv(cfg, f, f) {
                let _ = spawn_detached(&argv, None);
            }
        }
        ("save" | "edit", None) if !data.is_empty() => {
            let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0);
            let base = img::render_name(&cfg.filename, now, "region", "", "");
            if let Ok(saved) = img::save_atomic(&cfg.dir(), &base, ext, data) {
                if key == "edit" {
                    if let Some(argv) = editor_argv(cfg, &saved, &saved) {
                        let _ = spawn_detached(&argv, None);
                    }
                }
            }
        }
        _ => {}
    }
}

// ---- main --------------------------------------------------------------------------------------------

fn main() {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    match argv.first().map(String::as_str) {
        Some("__clip") => std::process::exit(resident_clip()),
        Some("__notify-wait") => std::process::exit(resident_notify(&argv[1..])),
        _ => {}
    }
    let a = match parse_args(&argv) {
        Ok(a) => a,
        Err(e) if e.is_empty() => {
            println!("{USAGE}");
            return;
        }
        Err(e) => {
            eprintln!("mios-shot: {e}\n{USAGE}");
            std::process::exit(64);
        }
    };
    let mut cfg = match Settings::load() {
        Ok(c) => c,
        Err(e) => {
            eprintln!("mios-shot: {e}");
            std::process::exit(1);
        }
    };
    if let Err(e) = cfg.apply(&a) {
        eprintln!("mios-shot: {e}");
        std::process::exit(64);
    }
    if a.mode == "probe" {
        println!("{}", serde_json::to_string_pretty(&probe(&cfg)).unwrap_or_default());
        return;
    }
    if cfg.delay_s > 0.0 {
        // Delay first, then freeze and select (selecting before the delay would capture a stale moment).
        std::thread::sleep(Duration::from_secs_f64(cfg.delay_s));
    }
    let res = shoot(&a, &cfg).map_err(|e| match e {
        Fail::Cancelled => (2, "cancelled".to_string()),
        Fail::Error(e) => (1, e),
    });
    let res = res.and_then(|shot| deliver(&a, &cfg, shot).map_err(|e| (1, e)));
    match res {
        Ok(j) => {
            if a.json {
                let line = j.to_string();
                if a.file.as_deref() == Some("-") {
                    eprintln!("{line}");
                } else {
                    println!("{line}");
                }
            } else if let Some(f) = j.get("file").and_then(Json::as_str) {
                if a.file.is_none() {
                    println!("{f}");
                }
            }
        }
        Err((code, msg)) => {
            if a.json {
                let j = json!({"ok": false, "cancelled": code == 2, "error": msg});
                if a.file.as_deref() == Some("-") { eprintln!("{j}") } else { println!("{j}") }
            }
            if code != 2 {
                eprintln!("mios-shot: {msg}");
            }
            std::process::exit(code);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn geometry_parses_slurp_and_plain() {
        assert_eq!(parse_geometry("10,20 30x40").unwrap(), Rect { x: 10.0, y: 20.0, w: 30.0, h: 40.0 });
        assert_eq!(parse_geometry("10 20 30 40").unwrap(), Rect { x: 10.0, y: 20.0, w: 30.0, h: 40.0 });
        assert!(parse_geometry("10,20 0x40").is_err());
        assert!(parse_geometry("nope").is_err());
    }

    #[test]
    fn args_modes_and_overrides() {
        let a = parse_args(&["window".into(), "--no-copy".into(), "--format=webp".into(), "--delay".into(), "2".into()]).unwrap();
        assert_eq!(a.mode, "window");
        assert_eq!(a.copy, Some(false));
        assert_eq!(a.format.as_deref(), Some("webp"));
        assert_eq!(a.delay, Some(2.0));
        assert_eq!(parse_args(&[]).unwrap().mode, "region");
        assert!(parse_args(&["--bogus".into()]).is_err());
        let q = parse_args(&["all".into(), "--quiet".into()]).unwrap();
        let t: toml::Value = toml::from_str("flash = true\nsound = true\nnotify = true").unwrap();
        let mut s = Settings::from_table(&t).unwrap();
        s.apply(&q).unwrap();
        assert!(!s.flash && !s.sound && !s.notify);
    }

    #[test]
    fn selection_lines() {
        assert!(matches!(parse_selection(r#"{"cancel":true}"#), Err(Fail::Cancelled)));
        let s = parse_selection(r#"{"kind":"region","rect":[1,2,3,4],"action":"copy"}"#).unwrap();
        assert_eq!(s.kind, Pick::Region(Rect { x: 1.0, y: 2.0, w: 3.0, h: 4.0 }));
        assert_eq!(s.action.as_deref(), Some("copy"));
        assert_eq!(parse_selection(r#"{"kind":"window","id":"abc"}"#).unwrap().kind, Pick::Window("abc".into()));
        assert!(parse_selection(r#"{"kind":"region","rect":[1,2,0,4]}"#).is_err());
        assert!(parse_selection(r#"{"kind":"region","rect":[1,2,3,4],"action":"rm -rf"}"#).is_err());
        assert!(parse_selection("garbage").is_err());
    }

    #[test]
    fn clipboard_framing_round_trips() {
        let f = frame_offers(&[("image/png", b"\x89PNG\n\x00"), ("text/plain", b"a\nb")]);
        let o = unframe_offers(&f).unwrap();
        assert_eq!(o, vec![("image/png".to_string(), b"\x89PNG\n\x00".to_vec()), ("text/plain".to_string(), b"a\nb".to_vec())]);
        assert!(unframe_offers(b"image/png\n9\nshort").is_err());
    }

    #[test]
    fn settings_defaults_and_table() {
        let t: toml::Value = toml::from_str("format = \"jpg\"\nquality = 70\ncopy = false\neditors = [[\"satty\", \"-f\", \"{file}\"], []]").unwrap();
        let s = Settings::from_table(&t).unwrap();
        assert_eq!(s.format, Format::Jpg);
        assert_eq!(s.quality, 70);
        assert!(!s.copy && s.save && s.freeze && !s.cursor);
        assert_eq!(s.editors, vec![vec!["satty".to_string(), "-f".into(), "{file}".into()]]);
        let bad: toml::Value = toml::from_str("format = \"bmp\"").unwrap();
        assert!(Settings::from_table(&bad).is_err());
    }

    #[test]
    fn percent_decoding() {
        assert_eq!(percent_decode("/run/a%20b.png"), "/run/a b.png");
        assert_eq!(percent_decode("/x%2"), "/x%2");
    }
}
