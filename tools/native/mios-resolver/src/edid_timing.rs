// AI-hint: EDID detailed timings (base block + CTA-861 extension DTDs, the display range limits descriptor) and the
//          extended-vblank derivation the frame-rate policy uses for a rate the panel does not advertise (DP-3: 90 Hz
//          on a 144 Hz panel): the fastest native timing with a longer vertical FRONT porch -- the horizontal timing
//          and (to within the whole-MHz clock a Hyprland 0.56 modeline can carry) the line rate of the 144 Hz timing,
//          only more blank lines per frame. That is what VRR does on every frame and what amdgpu's freesync_video
//          modes do. Modelines are parsed the way Hyprland programs them (clock truncated to whole MHz).
//          Pure: unit-tested on the real DP-3 (LG 24GL600F) descriptor bytes.
// AI-related: tools/native/mios-resolver/src/framerate.rs ([desktop.framerate].modes = "extended-vblank"),
// AI-related: src/mios-rs/miosd/src/framerate/plan.rs (mode fallback), src/mios-rs/miosd/src/framerate.rs (`miosd framerate modeline`)
// AI-functions: detailed_timings, range_limits, extended_vblank, extended_vblank_modeline, Timing::whole_mhz, Timing::modeline, Timing::parse_modeline, with_extra_dtd
//! EDID timings and extended-vblank modes.

/// One display timing (a DRM mode): pixel clock in kHz (EDID resolution: 10 kHz) and the horizontal / vertical
/// active, sync start, sync end and total, in pixels / lines.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Timing {
    pub clock_khz: u32,
    pub hdisplay: u32,
    pub hsync_start: u32,
    pub hsync_end: u32,
    pub htotal: u32,
    pub vdisplay: u32,
    pub vsync_start: u32,
    pub vsync_end: u32,
    pub vtotal: u32,
    pub hsync_positive: bool,
    pub vsync_positive: bool,
}

/// The display range limits descriptor (EDID tag 0xFD): vertical Hz and horizontal kHz the panel accepts.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct RangeLimits {
    pub min_v_hz: u32,
    pub max_v_hz: u32,
    pub min_h_khz: u32,
    pub max_h_khz: u32,
    /// maximum pixel clock in MHz (0 = not given)
    pub max_clock_mhz: u32,
}

/// Why no extended-vblank mode exists for a target rate.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum NoMode {
    /// the target is above the base timing's own rate (a longer frame can only LOWER the rate)
    AboveBase,
    /// the target is outside the panel's vertical range (its range limits descriptor)
    OutOfRange,
    /// the derived total does not fit a DRM / EDID timing field
    TooLong,
    /// rounding the line count lands further than `tolerance` from the target
    Imprecise,
}

impl Timing {
    /// Refresh rate in Hz.
    pub fn refresh_hz(&self) -> f64 {
        if self.htotal == 0 || self.vtotal == 0 {
            return 0.0;
        }
        f64::from(self.clock_khz) * 1000.0 / (f64::from(self.htotal) * f64::from(self.vtotal))
    }

    /// Horizontal line rate in kHz.
    pub fn line_khz(&self) -> f64 {
        if self.htotal == 0 {
            return 0.0;
        }
        f64::from(self.clock_khz) / f64::from(self.htotal)
    }

    /// The Hyprland / xrandr modeline: `modeline <MHz> hd hss hse ht vd vss vse vt +hsync +vsync`.
    pub fn modeline(&self) -> String {
        let mhz = format!("{:.3}", f64::from(self.clock_khz) / 1000.0);
        let mhz = mhz.trim_end_matches('0').trim_end_matches('.');
        format!(
            "modeline {mhz} {} {} {} {} {} {} {} {} {}hsync {}vsync",
            self.hdisplay,
            self.hsync_start,
            self.hsync_end,
            self.htotal,
            self.vdisplay,
            self.vsync_start,
            self.vsync_end,
            self.vtotal,
            if self.hsync_positive { "+" } else { "-" },
            if self.vsync_positive { "+" } else { "-" }
        )
    }

    /// The same timing with the pixel clock truncated to whole MHz: what Hyprland 0.56 programs for a modeline. Its
    /// parser (config/shared/monitor/Parser.cpp) reads the clock with `ASSIGN_OR_FAIL(mode.clock, float)` into
    /// `drmModeModeInfo.clock`, a `uint32_t`, and only then multiplies by 1000: "332.18" becomes 332000 kHz.
    pub fn whole_mhz(&self) -> Timing {
        Timing { clock_khz: self.clock_khz / 1000 * 1000, ..*self }
    }

    /// Parse a modeline (with or without the leading `modeline` word, as Hyprland's parser accepts it) into the timing
    /// Hyprland 0.56 programs from it: the clock's fraction of a MHz is dropped (`whole_mhz`), so the refresh is the
    /// one `hyprctl monitors` will report.
    pub fn parse_modeline(s: &str) -> Option<Timing> {
        let mut it = s.split_whitespace().peekable();
        if it.peek().is_some_and(|w| w.eq_ignore_ascii_case("modeline")) {
            it.next();
        }
        let mhz: f64 = it.next()?.parse().ok()?;
        let mut n = [0u32; 8];
        for v in &mut n {
            *v = it.next()?.parse().ok()?;
        }
        let mut hpos = true;
        let mut vpos = true;
        for flag in it {
            match flag.to_ascii_lowercase().as_str() {
                "+hsync" => hpos = true,
                "-hsync" => hpos = false,
                "+vsync" => vpos = true,
                "-vsync" => vpos = false,
                _ => return None,
            }
        }
        let t = Timing {
            // float -> uint32 (truncation), then * 1000: Hyprland's own arithmetic
            clock_khz: if mhz.is_finite() && mhz >= 1.0 && mhz < f64::from(u32::MAX / 1000) { (mhz.trunc() as u32) * 1000 } else { 0 },
            hdisplay: n[0],
            hsync_start: n[1],
            hsync_end: n[2],
            htotal: n[3],
            vdisplay: n[4],
            vsync_start: n[5],
            vsync_end: n[6],
            vtotal: n[7],
            hsync_positive: hpos,
            vsync_positive: vpos,
        };
        t.sane().then_some(t)
    }

    fn sane(&self) -> bool {
        self.clock_khz > 0
            && self.hdisplay > 0
            && self.hdisplay <= self.hsync_start
            && self.hsync_start <= self.hsync_end
            && self.hsync_end <= self.htotal
            && self.vdisplay > 0
            && self.vdisplay <= self.vsync_start
            && self.vsync_start <= self.vsync_end
            && self.vsync_end <= self.vtotal
    }

    /// An 18-byte EDID detailed timing descriptor for this timing (None when a field does not fit).
    pub fn to_dtd(&self) -> Option<[u8; 18]> {
        let clock = self.clock_khz / 10;
        let hblank = self.htotal.checked_sub(self.hdisplay)?;
        let vblank = self.vtotal.checked_sub(self.vdisplay)?;
        let hso = self.hsync_start - self.hdisplay;
        let hsw = self.hsync_end - self.hsync_start;
        let vso = self.vsync_start - self.vdisplay;
        let vsw = self.vsync_end - self.vsync_start;
        if clock == 0 || clock > 0xFFFF || self.clock_khz % 10 != 0 || self.hdisplay > 4095 || hblank > 4095
            || self.vdisplay > 4095 || vblank > 4095 || hso > 1023 || hsw > 1023 || vso > 63 || vsw > 63
        {
            return None;
        }
        let mut d = [0u8; 18];
        d[0] = (clock & 0xFF) as u8;
        d[1] = (clock >> 8) as u8;
        d[2] = (self.hdisplay & 0xFF) as u8;
        d[3] = (hblank & 0xFF) as u8;
        d[4] = (((self.hdisplay >> 8) << 4) | (hblank >> 8)) as u8;
        d[5] = (self.vdisplay & 0xFF) as u8;
        d[6] = (vblank & 0xFF) as u8;
        d[7] = (((self.vdisplay >> 8) << 4) | (vblank >> 8)) as u8;
        d[8] = (hso & 0xFF) as u8;
        d[9] = (hsw & 0xFF) as u8;
        d[10] = (((vso & 0x0F) << 4) | (vsw & 0x0F)) as u8;
        d[11] = (((hso >> 8) << 6) | ((hsw >> 8) << 4) | ((vso >> 4) << 2) | (vsw >> 4)) as u8;
        // image size and border unknown (0); digital separate sync with the polarities
        d[17] = 0x18 | if self.vsync_positive { 0x04 } else { 0 } | if self.hsync_positive { 0x02 } else { 0 };
        Some(d)
    }
}

/// One 18-byte descriptor as a timing (None for a display descriptor: pixel clock 0).
pub fn parse_dtd(d: &[u8]) -> Option<Timing> {
    if d.len() < 18 {
        return None;
    }
    let clock = u32::from(d[0]) | (u32::from(d[1]) << 8);
    if clock == 0 {
        return None;
    }
    let hdisplay = u32::from(d[2]) | ((u32::from(d[4]) >> 4) << 8);
    let hblank = u32::from(d[3]) | ((u32::from(d[4]) & 0x0F) << 8);
    let vdisplay = u32::from(d[5]) | ((u32::from(d[7]) >> 4) << 8);
    let vblank = u32::from(d[6]) | ((u32::from(d[7]) & 0x0F) << 8);
    let hso = u32::from(d[8]) | (((u32::from(d[11]) >> 6) & 3) << 8);
    let hsw = u32::from(d[9]) | (((u32::from(d[11]) >> 4) & 3) << 8);
    let vso = (u32::from(d[10]) >> 4) | (((u32::from(d[11]) >> 2) & 3) << 4);
    let vsw = (u32::from(d[10]) & 0x0F) | ((u32::from(d[11]) & 3) << 4);
    // interlaced timings (bit 7 of the flags) are never a base for a progressive mode
    if d[17] & 0x80 != 0 {
        return None;
    }
    let digital_separate = d[17] & 0x18 == 0x18;
    let t = Timing {
        clock_khz: clock * 10,
        hdisplay,
        hsync_start: hdisplay + hso,
        hsync_end: hdisplay + hso + hsw,
        htotal: hdisplay + hblank,
        vdisplay,
        vsync_start: vdisplay + vso,
        vsync_end: vdisplay + vso + vsw,
        vtotal: vdisplay + vblank,
        hsync_positive: !digital_separate || d[17] & 0x02 != 0,
        vsync_positive: !digital_separate || d[17] & 0x04 != 0,
    };
    t.sane().then_some(t)
}

fn blocks(edid: &[u8]) -> impl Iterator<Item = &[u8]> {
    edid.chunks_exact(128)
}

/// Every detailed timing descriptor of an EDID: the base block's four descriptor slots, then each CTA-861 extension's
/// DTDs (in the panel's order; the first is the preferred mode).
pub fn detailed_timings(edid: &[u8]) -> Vec<Timing> {
    let mut out = Vec::new();
    for (i, b) in blocks(edid).enumerate() {
        if i == 0 {
            if b[..8] != [0, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0] {
                return out;
            }
            out.extend((0..4).filter_map(|k| parse_dtd(&b[54 + 18 * k..72 + 18 * k])));
        } else if b[0] == 0x02 {
            let start = usize::from(b[2]);
            if (4..=127).contains(&start) {
                let mut off = start;
                while off + 18 <= 127 {
                    match parse_dtd(&b[off..off + 18]) {
                        Some(t) => out.push(t),
                        None => break,
                    }
                    off += 18;
                }
            }
        }
    }
    out
}

/// The display range limits descriptor of the base block, with the EDID 1.4 +255 offsets applied.
pub fn range_limits(edid: &[u8]) -> Option<RangeLimits> {
    let b = blocks(edid).next()?;
    (0..4).map(|k| &b[54 + 18 * k..72 + 18 * k]).find(|d| d[..4] == [0, 0, 0, 0xFD]).map(|d| {
        let off = |bit_lo: u8, bit_hi: u8, v: u8, which_max: bool| {
            let flag = if which_max { d[4] & bit_hi != 0 } else { d[4] & bit_lo != 0 && d[4] & bit_hi != 0 };
            u32::from(v) + if flag { 255 } else { 0 }
        };
        RangeLimits {
            min_v_hz: off(0x01, 0x02, d[5], false),
            max_v_hz: off(0x01, 0x02, d[6], true),
            min_h_khz: off(0x04, 0x08, d[7], false),
            max_h_khz: off(0x04, 0x08, d[8], true),
            max_clock_mhz: u32::from(d[9]) * 10,
        }
    })
}

/// The base timing for extended-vblank modes at `w`x`h`: the fastest detailed timing at that size.
pub fn fastest_at(timings: &[Timing], w: u32, h: u32) -> Option<Timing> {
    timings
        .iter()
        .filter(|t| t.hdisplay == w && t.vdisplay == h)
        .max_by(|a, b| a.refresh_hz().partial_cmp(&b.refresh_hz()).unwrap_or(std::cmp::Ordering::Equal))
        .copied()
}

/// Which vertical porch absorbs the added blank lines.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Porch {
    /// the front porch (before the sync pulse): what VRR does on every frame and amdgpu freesync_video modes use --
    /// the modeline form. Not representable in an EDID DTD (its vertical sync offset is 6 bits, at most 63 lines).
    Front,
    /// the back porch (after the sync pulse): representable in an EDID DTD, the EDID-override form
    Back,
}

/// `base` slowed to `target_hz` by adding blank lines to one vertical porch only (the sync pulse keeps its length;
/// the pixel clock, the horizontal timing and so the line rate are unchanged). `tolerance_hz` bounds the rounding
/// error; `limits` (the panel's range descriptor) must contain the target. A line count can only grow, so the result
/// is never faster than `base`.
pub fn extended_vblank(base: &Timing, target_hz: f64, tolerance_hz: f64, limits: Option<&RangeLimits>, porch: Porch) -> Result<Timing, NoMode> {
    if !(target_hz.is_finite() && target_hz > 0.0) || target_hz > base.refresh_hz() + 1e-6 {
        return Err(NoMode::AboveBase);
    }
    if let Some(l) = limits {
        if l.min_v_hz > 0 && (target_hz + tolerance_hz < f64::from(l.min_v_hz) || target_hz - tolerance_hz > f64::from(l.max_v_hz)) {
            return Err(NoMode::OutOfRange);
        }
    }
    let vtotal = (f64::from(base.clock_khz) * 1000.0 / (f64::from(base.htotal) * target_hz)).round() as u32;
    let grow = vtotal.saturating_sub(base.vtotal);
    // EDID vblank is 12 bits (and a DRM mode's vtotal 16): longer frames fit no timing a driver takes
    if vtotal.saturating_sub(base.vdisplay) > 4095 {
        return Err(NoMode::TooLong);
    }
    let t = match porch {
        Porch::Front => Timing { vsync_start: base.vsync_start + grow, vsync_end: base.vsync_end + grow, vtotal, ..*base },
        Porch::Back => Timing { vtotal, ..*base },
    };
    if (t.refresh_hz() - target_hz).abs() > tolerance_hz {
        return Err(NoMode::Imprecise);
    }
    Ok(t)
}

/// The extended-vblank MODELINE for Hyprland: `base` with its clock truncated to whole MHz first (the only clocks a
/// Hyprland 0.56 modeline can carry, see `Timing::whole_mhz`), then slowed to `target_hz` through the front porch. The
/// line rate drops by under 1 MHz / htotal (DP-3: 161.566 -> 161.479 kHz, -0.05 %); the refresh is what Hyprland
/// programs and reports (DP-3 at 90: 332 MHz, vtotal 1794, 90.010 Hz). The clock never rises, so a panel's maximum
/// pixel clock still holds.
pub fn extended_vblank_modeline(base: &Timing, target_hz: f64, tolerance_hz: f64, limits: Option<&RangeLimits>) -> Result<Timing, NoMode> {
    let q = base.whole_mhz();
    if q.clock_khz == 0 {
        return Err(NoMode::Imprecise);
    }
    extended_vblank(&q, target_hz, tolerance_hz, limits, Porch::Front)
}

/// `edid` with `t` added as a detailed timing descriptor (an EDID override blob for drivers that accept only EDID
/// modes): into the first CTA-861 extension's free DTD space when there is room, else replacing a base-block dummy
/// descriptor (tag 0x10). Checksums are recomputed. None when there is no free slot or `t` does not fit a DTD.
pub fn with_extra_dtd(edid: &[u8], t: &Timing) -> Option<Vec<u8>> {
    let dtd = t.to_dtd()?;
    if edid.len() < 128 || edid.len() % 128 != 0 {
        return None;
    }
    let mut out = edid.to_vec();
    let fix = |blk: &mut [u8]| {
        let sum: u32 = blk[..127].iter().map(|b| u32::from(*b)).sum();
        blk[127] = ((256 - sum % 256) % 256) as u8;
    };
    for i in 1..out.len() / 128 {
        let blk = &mut out[i * 128..(i + 1) * 128];
        if blk[0] != 0x02 {
            continue;
        }
        let start = usize::from(blk[2]);
        if !(4..=127).contains(&start) {
            continue;
        }
        let mut off = start;
        while off + 18 <= 127 && parse_dtd(&blk[off..off + 18]).is_some() {
            off += 18;
        }
        // the slot must be padding (all zero up to the checksum) to be free
        if off + 18 <= 127 && blk[off..off + 18].iter().all(|b| *b == 0) {
            blk[off..off + 18].copy_from_slice(&dtd);
            // the number of native DTDs (low nibble of byte 3) is a hint only; leave it
            fix(blk);
            return Some(out);
        }
    }
    let base = &mut out[..128];
    for k in 0..4 {
        let o = 54 + 18 * k;
        if base[o..o + 4] == [0, 0, 0, 0x10] {
            base[o..o + 18].copy_from_slice(&dtd);
            fix(base);
            return Some(out);
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    /// DP-3 on this host (LG 24GL600F): the base block's first DTD (the preferred 144 Hz mode) and its range
    /// limits descriptor, as read from /sys/class/drm/card0-DP-3/edid (identity bytes zeroed).
    fn dp3_edid() -> Vec<u8> {
        let mut b = vec![0u8; 256];
        b[..8].copy_from_slice(&[0, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0]);
        b[54..72].copy_from_slice(&[0xc2, 0x81, 0x80, 0x88, 0x70, 0x38, 0x2a, 0x40, 0x18, 0x20, 0x35, 0x00, 0x13, 0x2a, 0x21, 0x00, 0x00, 0x1e]);
        b[72..90].copy_from_slice(&[0x02, 0x3a, 0x80, 0x18, 0x71, 0x38, 0x2d, 0x40, 0x58, 0x2c, 0x45, 0x00, 0x13, 0x2a, 0x21, 0x00, 0x00, 0x1e]);
        b[90..108].copy_from_slice(&[0, 0, 0, 0xfd, 0x00, 0x30, 0x90, 0xa0, 0xa0, 0x22, 0x01, 0x0a, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20]);
        b[108..126].copy_from_slice(&[0, 0, 0, 0x10, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]);
        b[126] = 1;
        // CTA extension: DTDs from offset 0x18 (as on the panel), one 120 Hz DTD then padding
        b[128] = 0x02;
        b[129] = 0x03;
        b[130] = 0x18;
        b[128 + 0x18..128 + 0x18 + 18].copy_from_slice(&[0x86, 0x6f, 0x80, 0xa0, 0x70, 0x38, 0x40, 0x40, 0x30, 0x20, 0x35, 0x00, 0x13, 0x2a, 0x21, 0x00, 0x00, 0x1e]);
        for blk in b.chunks_exact_mut(128) {
            let sum: u32 = blk[..127].iter().map(|x| u32::from(*x)).sum();
            blk[127] = ((256 - sum % 256) % 256) as u8;
        }
        b
    }

    #[test]
    fn dp3_timings_and_range_parse() {
        let e = dp3_edid();
        let t = detailed_timings(&e);
        assert_eq!(t.len(), 3, "two base DTDs and one CTA DTD");
        assert_eq!(t[0].modeline(), "modeline 332.18 1920 1944 1976 2056 1080 1083 1088 1122 +hsync +vsync");
        assert!((t[0].refresh_hz() - 143.998).abs() < 0.001, "{}", t[0].refresh_hz());
        assert!((t[1].refresh_hz() - 60.0).abs() < 0.01, "the 148.5 MHz 60 Hz DTD: {}", t[1].refresh_hz());
        let r = range_limits(&e).unwrap();
        assert_eq!(r, RangeLimits { min_v_hz: 48, max_v_hz: 144, min_h_khz: 160, max_h_khz: 160, max_clock_mhz: 340 });
        assert_eq!(fastest_at(&t, 1920, 1080), Some(t[0]));
        assert_eq!(fastest_at(&t, 1280, 720), None);
    }

    #[test]
    fn the_hyprland_modeline_has_a_whole_mhz_clock_and_reports_90_010() {
        let e = dp3_edid();
        let base = fastest_at(&detailed_timings(&e), 1920, 1080).unwrap();
        let t = extended_vblank_modeline(&base, 90.0, 0.05, range_limits(&e).as_ref()).unwrap();
        assert_eq!(t.modeline(), "modeline 332 1920 1944 1976 2056 1080 1755 1760 1794 +hsync +vsync");
        assert_eq!(t.clock_khz, 332_000);
        // what Hyprland programs from that string is exactly this timing (a whole-MHz clock survives its parse)
        assert_eq!(Timing::parse_modeline(&t.modeline()), Some(t));
        assert!((t.refresh_hz() - 90.0102).abs() < 0.0005, "{}", t.refresh_hz());
        assert!((t.line_khz() - 161.479).abs() < 0.001, "{}", t.line_khz());
        assert!(t.line_khz() <= base.line_khz() && (base.line_khz() - t.line_khz()) / base.line_khz() < 0.001, "the line rate drops by < 0.1 %");
        assert!(t.clock_khz <= base.clock_khz, "never above the panel's own clock");
        // horizontal timing, sync pulse and back porch kept; only the front porch grew
        assert_eq!((t.hsync_start, t.hsync_end, t.htotal), (base.hsync_start, base.hsync_end, base.htotal));
        assert_eq!((t.vsync_end - t.vsync_start, t.vtotal - t.vsync_end), (base.vsync_end - base.vsync_start, base.vtotal - base.vsync_end));
        // every tier rate the panel's range allows, each landing where Hyprland reports it
        for hz in [48.0, 60.0, 75.0, 90.0, 100.0, 120.0] {
            let m = extended_vblank_modeline(&base, hz, 0.05, range_limits(&e).as_ref()).unwrap();
            let shown = Timing::parse_modeline(&m.modeline()).unwrap().refresh_hz();
            assert!((shown - hz).abs() <= 0.05, "{hz}: Hyprland would program {shown}");
        }
    }

    #[test]
    fn negative_control_a_fractional_mhz_modeline_is_not_what_hyprland_programs() {
        // the pre-fix derivation: the 144 Hz clock 332.18 MHz kept, vtotal 1795 -> 90.009 Hz on paper
        let e = dp3_edid();
        let base = fastest_at(&detailed_timings(&e), 1920, 1080).unwrap();
        let paper = extended_vblank(&base, 90.0, 0.05, range_limits(&e).as_ref(), Porch::Front).unwrap();
        assert_eq!(paper.modeline(), "modeline 332.18 1920 1944 1976 2056 1080 1756 1761 1795 +hsync +vsync");
        assert!((paper.refresh_hz() - 90.0086).abs() < 0.0005);
        // Hyprland 0.56 parses "332.18" into a uint32 (332) and multiplies by 1000: 89.960 Hz at 161.479 kHz
        let programmed = Timing::parse_modeline(&paper.modeline()).unwrap();
        assert_eq!(programmed.clock_khz, 332_000);
        assert!((programmed.refresh_hz() - 89.9604).abs() < 0.0005, "{}", programmed.refresh_hz());
        assert_ne!(programmed, paper, "the paper timing is not the one on the wire");
        // a clock below 1 MHz (0 after the cast) or junk never parses
        assert_eq!(Timing::parse_modeline("0.5 1920 1944 1976 2056 1080 1083 1088 1122"), None);
        assert_eq!(Timing::parse_modeline("inf 1920 1944 1976 2056 1080 1083 1088 1122"), None);
    }

    #[test]
    fn ninety_hz_is_the_144_timing_with_a_longer_front_porch() {
        let e = dp3_edid();
        let base = fastest_at(&detailed_timings(&e), 1920, 1080).unwrap();
        let t = extended_vblank(&base, 90.0, 0.05, range_limits(&e).as_ref(), Porch::Front).unwrap();
        assert_eq!(t.modeline(), "modeline 332.18 1920 1944 1976 2056 1080 1756 1761 1795 +hsync +vsync");
        assert!((t.refresh_hz() - 90.0086).abs() < 0.0005, "{}", t.refresh_hz());
        // the line rate is the 144 Hz mode's (inside the panel's 160 kHz +-1% HorizSync window)
        assert_eq!(t.line_khz(), base.line_khz());
        assert!((t.line_khz() - 161.566).abs() < 0.001);
        // only the front porch grew: sync pulse width and back porch kept
        assert_eq!((t.vsync_end - t.vsync_start, t.vtotal - t.vsync_end), (base.vsync_end - base.vsync_start, base.vtotal - base.vsync_end));
        // a front porch of 676 lines cannot be a DTD (6-bit sync offset); the back-porch form can
        assert_eq!(t.to_dtd(), None);
        let b = extended_vblank(&base, 90.0, 0.05, range_limits(&e).as_ref(), Porch::Back).unwrap();
        assert_eq!(b.modeline(), "modeline 332.18 1920 1944 1976 2056 1080 1083 1088 1795 +hsync +vsync");
        assert_eq!(parse_dtd(&b.to_dtd().unwrap()), Some(b), "the back-porch form round-trips through a DTD");
        // every tier rate below 144 the panel's range allows
        for hz in [48.0, 60.0, 75.0, 90.0, 100.0, 120.0] {
            for porch in [Porch::Front, Porch::Back] {
                let m = extended_vblank(&base, hz, 0.05, range_limits(&e).as_ref(), porch).unwrap();
                assert!((m.refresh_hz() - hz).abs() <= 0.05, "{hz}: {}", m.refresh_hz());
            }
        }
    }

    #[test]
    fn negative_controls_refuse_what_the_panel_or_a_dtd_cannot_carry() {
        let e = dp3_edid();
        let lim = range_limits(&e);
        let base = fastest_at(&detailed_timings(&e), 1920, 1080).unwrap();
        let f = Porch::Front;
        assert_eq!(extended_vblank(&base, 165.0, 0.05, lim.as_ref(), f), Err(NoMode::AboveBase), "a longer frame never speeds up");
        assert_eq!(extended_vblank(&base, 40.0, 0.05, lim.as_ref(), f), Err(NoMode::OutOfRange), "below the 48 Hz VRR floor");
        assert_eq!(extended_vblank(&base, 40.0, 0.05, None, f).unwrap().vtotal, 4039, "without a range descriptor only the line limits apply");
        assert_eq!(extended_vblank(&base, 89.9937, 0.0001, lim.as_ref(), f), Err(NoMode::Imprecise), "a line is 6.2 us: 90.0086 is the nearest");
        assert_eq!(extended_vblank(&base, 10.0, 0.05, None, f), Err(NoMode::TooLong));
        assert_eq!(extended_vblank(&base, f64::NAN, 0.05, None, f), Err(NoMode::AboveBase));
    }

    #[test]
    fn modeline_round_trips_and_rejects_garbage() {
        let t = Timing::parse_modeline("modeline 332 1920 1944 1976 2056 1080 1755 1760 1794 +hsync +vsync").unwrap();
        assert_eq!(t.vtotal, 1794);
        assert_eq!(Timing::parse_modeline(&t.modeline()), Some(t));
        assert_eq!(Timing::parse_modeline("modeline 332.18 1920 1944 1976 2056 1080 1756 1761 1795 +hsync +vsync").map(|t| t.clock_khz), Some(332_000), "truncated, as Hyprland does");
        assert_eq!(Timing::parse_modeline("148.5 1920 2008 2052 2200 1080 1084 1089 1125 -hsync +vsync").map(|t| t.hsync_positive), Some(false));
        for bad in ["modeline", "modeline 332 1920 1944", "modeline x 1 2 3 4 5 6 7 8", "modeline 1 100 50 60 70 10 11 12 13", "modeline 1 1 1 1 1 1 1 1 1 +wat"] {
            assert_eq!(Timing::parse_modeline(bad), None, "{bad}");
        }
    }

    #[test]
    fn override_blob_carries_the_new_dtd_with_valid_checksums() {
        let e = dp3_edid();
        let base = fastest_at(&detailed_timings(&e), 1920, 1080).unwrap();
        let t = extended_vblank(&base, 90.0, 0.05, None, Porch::Back).unwrap();
        assert_eq!(with_extra_dtd(&e, &extended_vblank(&base, 90.0, 0.05, None, Porch::Front).unwrap()), None, "front porch: no DTD");
        let out = with_extra_dtd(&e, &t).unwrap();
        assert_eq!(out.len(), e.len());
        for blk in out.chunks_exact(128) {
            assert_eq!(blk.iter().map(|b| u32::from(*b)).sum::<u32>() % 256, 0, "checksum");
        }
        let all = detailed_timings(&out);
        assert_eq!(all.len(), 4);
        assert_eq!(all[3], t, "appended after the extension's own DTDs");
        assert_eq!(all[..3], detailed_timings(&e)[..], "the panel's own timings are unchanged (the preferred mode stays first)");
        assert_eq!(range_limits(&out), range_limits(&e));
        // no CTA room: the base block's dummy descriptor is used
        let mut base_only = e[..128].to_vec();
        base_only[126] = 0;
        let s: u32 = base_only[..127].iter().map(|b| u32::from(*b)).sum();
        base_only[127] = ((256 - s % 256) % 256) as u8;
        let b2 = with_extra_dtd(&base_only, &t).unwrap();
        assert_eq!(detailed_timings(&b2).last(), Some(&t));
        // negative control: no dummy slot and no extension
        let mut full = base_only.clone();
        full[108..126].copy_from_slice(&e[54..72]);
        assert_eq!(with_extra_dtd(&full, &t), None);
        assert_eq!(with_extra_dtd(&e[..100], &t), None);
    }
}
