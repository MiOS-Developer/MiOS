//! H.264 encoders behind one trait. Operator decision 2026-10-17: Cisco OpenH264 (software, the
//! binary from fedora-cisco-openh264, loaded with dlopen so its patent licence applies), made fast
//! with slice threads and damage-aware encoding. No VA-API path is implemented or auto-selected;
//! the trait keeps room for one if the operator asks for it later.
//!
//! Unsafe: the OpenH264 C ABI (vtable calls) only.
#![allow(unsafe_code)]

use std::ffi::c_void;
use std::ptr::{addr_of_mut, null_mut};

use anyhow::{Result, bail};
use openh264_sys2::{API as _, DynamicAPI};
use tracing::info;

/// One I420 picture: Y rows (stride), then `height/2` rows of [U (stride/2) | V (stride/2)].
pub struct I420<'a> {
    pub data: &'a [u8],
    pub width: u32,
    pub height: u32,
    pub stride: u32,
}

impl I420<'_> {
    pub fn u_offset(&self) -> usize {
        (self.stride * self.height) as usize
    }
    pub fn v_offset(&self) -> usize {
        self.u_offset() + (self.stride / 2) as usize
    }
    /// Chroma row stride in the packed layout.
    pub fn uv_stride(&self) -> u32 {
        self.stride
    }
}

#[derive(Debug)]
pub struct Encoded {
    pub data: Vec<u8>,
    pub idr: bool,
    pub skipped: bool,
    pub encode_us: u64,
}

#[derive(Clone, Debug)]
pub struct EncoderSettings {
    pub width: u32,
    pub height: u32,
    pub fps: f32,
    pub bitrate_bps: u32,
    pub max_bitrate_bps: u32,
    pub qp_min: i32,
    pub qp_max: i32,
    pub threads: u16,
    /// "low" | "medium" | "high"
    pub complexity: String,
    pub full_range: bool,
}

pub trait H264Encoder: Send {
    #[allow(dead_code)] // part of the backend contract (a later encoder reports itself)
    fn name(&self) -> &'static str;
    fn encode(&mut self, pic: &I420<'_>, timestamp_ms: i64) -> Result<Encoded>;
    fn force_idr(&mut self);
    fn set_bitrate(&mut self, bps: u32);
    #[allow(dead_code)]
    fn settings(&self) -> &EncoderSettings;
}

/// Cisco OpenH264 via dlopen.
pub struct OpenH264 {
    api: DynamicAPI,
    enc: *mut openh264_sys2::ISVCEncoder,
    bs: Box<openh264_sys2::SFrameBSInfo>,
    settings: EncoderSettings,
    force_idr: bool,
    pub library: String,
    pub version: String,
}

// SAFETY: the encoder instance is used from one thread at a time (moved into the encode thread).
unsafe impl Send for OpenH264 {}

fn soname_candidates(cfg: &str) -> Vec<String> {
    if cfg != "auto" && !cfg.is_empty() {
        return vec![cfg.to_owned()];
    }
    vec!["libopenh264.so.8".into(), "libopenh264.so.7".into(), "/usr/lib64/libopenh264.so.8".into()]
}

pub fn load_api(library: &str) -> Result<(DynamicAPI, String, String)> {
    let mut errs = Vec::new();
    for cand in soname_candidates(library) {
        // SAFETY: the Cisco/Fedora OpenH264 2.x C ABI the bindings were generated for; the version is
        // checked right after loading and reported by --self-test.
        match unsafe { DynamicAPI::from_blob_path_unchecked(&cand) } {
            Ok(api) => {
                // SAFETY: plain function call into the loaded library.
                let v = unsafe { api.WelsGetCodecVersion() };
                let version = format!("{}.{}.{}", v.uMajor, v.uMinor, v.uRevision);
                if v.uMajor != 2 {
                    errs.push(format!("{cand}: OpenH264 {version} (need 2.x)"));
                    continue;
                }
                return Ok((api, cand, version));
            }
            Err(e) => errs.push(format!("{cand}: {e}")),
        }
    }
    bail!("Cisco OpenH264 not loadable ({}); install openh264 from fedora-cisco-openh264", errs.join("; "))
}

macro_rules! vcall {
    ($enc:expr, $f:ident $(, $a:expr)*) => {{
        let vt = *$enc;
        ((*vt).$f.expect(stringify!($f)))($enc $(, $a)*)
    }};
}

impl OpenH264 {
    pub fn new(library: &str, settings: EncoderSettings) -> Result<Self> {
        let (api, library, version) = load_api(library)?;
        let mut enc: *mut openh264_sys2::ISVCEncoder = null_mut();
        // SAFETY: OpenH264 encoder construction contract.
        unsafe {
            if api.WelsCreateSVCEncoder(&mut enc) != 0 || enc.is_null() {
                bail!("WelsCreateSVCEncoder failed");
            }
        }
        let mut me = Self {
            api,
            enc,
            // SAFETY: plain C struct, zero is a valid initial state.
            bs: Box::new(unsafe { std::mem::zeroed() }),
            settings,
            force_idr: true,
            library,
            version,
        };
        me.init()?;
        Ok(me)
    }

    fn init(&mut self) -> Result<()> {
        let s = &self.settings;
        if s.width == 0 || s.height == 0 || s.width % 2 != 0 || s.height % 2 != 0 {
            bail!("H.264 needs even dimensions, got {}x{}", s.width, s.height);
        }
        // SAFETY: GetDefaultParams fills the struct; then InitializeExt reads it.
        unsafe {
            let mut p: openh264_sys2::SEncParamExt = std::mem::zeroed();
            if vcall!(self.enc, GetDefaultParams, &mut p) != 0 {
                bail!("GetDefaultParams failed");
            }
            p.iUsageType = openh264_sys2::SCREEN_CONTENT_REAL_TIME;
            p.iPicWidth = s.width as i32;
            p.iPicHeight = s.height as i32;
            p.iTargetBitrate = s.bitrate_bps as i32;
            p.iMaxBitrate = s.max_bitrate_bps as i32;
            p.iRCMode = openh264_sys2::RC_BITRATE_MODE;
            p.fMaxFrameRate = s.fps;
            p.iTemporalLayerNum = 1;
            p.iSpatialLayerNum = 1;
            p.iComplexityMode = match s.complexity.as_str() {
                "high" => openh264_sys2::HIGH_COMPLEXITY,
                "medium" => openh264_sys2::MEDIUM_COMPLEXITY,
                _ => openh264_sys2::LOW_COMPLEXITY,
            };
            p.uiIntraPeriod = 0; // IDR only on demand (start, resize, refresh request)
            p.iNumRefFrame = 1;
            p.eSpsPpsIdStrategy = openh264_sys2::CONSTANT_ID;
            p.bEnableFrameSkip = true;
            p.iMaxQp = s.qp_max;
            p.iMinQp = s.qp_min;
            p.iMultipleThreadIdc = s.threads.max(1);
            p.bUseLoadBalancing = true;
            p.iLoopFilterDisableIdc = 0;
            p.bEnableDenoise = false;
            p.bEnableBackgroundDetection = true;
            p.bEnableAdaptiveQuant = true;
            p.bEnableFrameCroppingFlag = true;
            p.bEnableSceneChangeDetect = false; // a scene change must not cost an unrequested I frame
            p.bEnableLongTermReference = false;
            p.iEntropyCodingModeFlag = 0; // CAVLC: Constrained Baseline, what every EGFX decoder takes
            p.bIsLosslessLink = false;
            p.bFixRCOverShoot = true;
            let l = &mut p.sSpatialLayers[0];
            l.iVideoWidth = s.width as i32;
            l.iVideoHeight = s.height as i32;
            l.fFrameRate = s.fps;
            l.iSpatialBitrate = s.bitrate_bps as i32;
            l.iMaxSpatialBitrate = s.max_bitrate_bps as i32;
            l.uiProfileIdc = openh264_sys2::PRO_BASELINE;
            l.uiLevelIdc = 0; // auto from size and rate (1104x2080@90 -> 5.1)
            l.bVideoSignalTypePresent = true;
            l.uiVideoFormat = 5; // unspecified
            l.bFullRange = s.full_range;
            l.bColorDescriptionPresent = true;
            l.uiColorPrimaries = 1; // BT.709
            l.uiTransferCharacteristics = 1;
            l.uiColorMatrix = 1;
            if s.threads > 1 {
                l.sSliceArgument.uiSliceMode = openh264_sys2::SM_FIXEDSLCNUM_SLICE;
                l.sSliceArgument.uiSliceNum = u32::from(s.threads);
            } else {
                l.sSliceArgument.uiSliceMode = openh264_sys2::SM_SINGLE_SLICE;
                l.sSliceArgument.uiSliceNum = 1;
            }
            if vcall!(self.enc, InitializeExt, &p) != 0 {
                bail!("OpenH264 InitializeExt failed for {}x{} threads {}", s.width, s.height, s.threads);
            }
            let mut fmt: i32 = openh264_sys2::videoFormatI420 as i32;
            vcall!(self.enc, SetOption, openh264_sys2::ENCODER_OPTION_DATAFORMAT, addr_of_mut!(fmt).cast::<c_void>());
            let mut lvl: i32 = openh264_sys2::WELS_LOG_ERROR as i32;
            vcall!(self.enc, SetOption, openh264_sys2::ENCODER_OPTION_TRACE_LEVEL, addr_of_mut!(lvl).cast::<c_void>());
        }
        info!(
            w = self.settings.width,
            h = self.settings.height,
            fps = self.settings.fps,
            kbps = self.settings.bitrate_bps / 1000,
            threads = self.settings.threads,
            library = %self.library,
            version = %self.version,
            "OpenH264 encoder initialised"
        );
        self.force_idr = true;
        Ok(())
    }

    /// Re-initialise for a new size (resize/rotation); the next frame is an IDR.
    pub fn reconfigure(&mut self, width: u32, height: u32) -> Result<()> {
        // SAFETY: Uninitialize before re-init is the documented reconfiguration path.
        unsafe {
            vcall!(self.enc, Uninitialize);
        }
        self.settings.width = width;
        self.settings.height = height;
        self.init()
    }
}

impl H264Encoder for OpenH264 {
    fn name(&self) -> &'static str {
        "openh264"
    }

    fn encode(&mut self, pic: &I420<'_>, timestamp_ms: i64) -> Result<Encoded> {
        if pic.width != self.settings.width || pic.height != self.settings.height {
            self.reconfigure(pic.width, pic.height)?;
        }
        let t0 = std::time::Instant::now();
        let mut idr_forced = false;
        // SAFETY: the source picture points into `pic.data`, which outlives the call; OpenH264 only
        // reads the planes. The bitstream buffers are owned by the encoder until the next call.
        unsafe {
            if self.force_idr {
                vcall!(self.enc, ForceIntraFrame, true);
                self.force_idr = false;
                idr_forced = true;
            }
            let base = pic.data.as_ptr().cast_mut();
            let src = openh264_sys2::SSourcePicture {
                iColorFormat: openh264_sys2::videoFormatI420 as i32,
                iStride: [pic.stride as i32, pic.uv_stride() as i32, pic.uv_stride() as i32, 0],
                pData: [base, base.add(pic.u_offset()), base.add(pic.v_offset()), null_mut()],
                iPicWidth: pic.width as i32,
                iPicHeight: pic.height as i32,
                uiTimeStamp: timestamp_ms,
                bPsnrY: false,
                bPsnrU: false,
                bPsnrV: false,
            };
            let rc = vcall!(self.enc, EncodeFrame, &src, &mut *self.bs);
            if rc != 0 {
                bail!("OpenH264 EncodeFrame failed ({rc})");
            }
            let bs = &*self.bs;
            let skipped = bs.eFrameType == openh264_sys2::videoFrameTypeSkip;
            let idr = bs.eFrameType == openh264_sys2::videoFrameTypeIDR;
            let mut data = Vec::with_capacity(bs.iFrameSizeInBytes.max(0) as usize);
            if !skipped {
                for l in 0..(bs.iLayerNum.max(0) as usize).min(bs.sLayerInfo.len()) {
                    let layer = &bs.sLayerInfo[l];
                    let mut len = 0usize;
                    for n in 0..layer.iNalCount.max(0) as usize {
                        len += *layer.pNalLengthInByte.add(n) as usize;
                    }
                    data.extend_from_slice(std::slice::from_raw_parts(layer.pBsBuf, len));
                }
            }
            if idr_forced && skipped {
                // the rate controller skipped the forced IDR: ask again next frame
                self.force_idr = true;
            }
            Ok(Encoded { data, idr, skipped, encode_us: t0.elapsed().as_micros() as u64 })
        }
    }

    fn force_idr(&mut self) {
        self.force_idr = true;
    }

    fn set_bitrate(&mut self, bps: u32) {
        let bps = bps.clamp(100_000, self.settings.max_bitrate_bps.max(100_000));
        if bps == self.settings.bitrate_bps {
            return;
        }
        self.settings.bitrate_bps = bps;
        // SAFETY: SBitrateInfo option on a live encoder.
        unsafe {
            let mut b = openh264_sys2::SBitrateInfo { iLayer: openh264_sys2::SPATIAL_LAYER_ALL, iBitrate: bps as i32 };
            vcall!(self.enc, SetOption, openh264_sys2::ENCODER_OPTION_BITRATE, addr_of_mut!(b).cast::<c_void>());
        }
    }

    fn settings(&self) -> &EncoderSettings {
        &self.settings
    }
}

impl Drop for OpenH264 {
    fn drop(&mut self) {
        // SAFETY: tear-down of the encoder created in new().
        unsafe {
            vcall!(self.enc, Uninitialize);
            self.api.WelsDestroySVCEncoder(self.enc);
        }
    }
}

/// Minimal Annex B walker: (nal_unit_type, payload) for each NAL.
pub fn nal_units(data: &[u8]) -> Vec<(u8, &[u8])> {
    let mut out = Vec::new();
    let mut i = 0;
    let mut starts = Vec::new();
    while i + 3 <= data.len() {
        if data[i] == 0 && data[i + 1] == 0 && data[i + 2] == 1 {
            starts.push(i + 3);
            i += 3;
        } else {
            i += 1;
        }
    }
    for (k, s) in starts.iter().enumerate() {
        let mut e = starts.get(k + 1).map_or(data.len(), |n| n - 3);
        while e > *s && data[e - 1] == 0 {
            e -= 1;
        }
        if *s < e {
            out.push((data[*s] & 0x1f, &data[*s..e]));
        }
    }
    out
}

/// (profile_idc, level_idc) from the first SPS in an Annex B stream.
pub fn sps_profile_level(data: &[u8]) -> Option<(u8, u8)> {
    nal_units(data).into_iter().find(|(t, _)| *t == 7).and_then(|(_, p)| (p.len() >= 4).then(|| (p[1], p[3])))
}

/// Count of slices (VCL NALs) in a frame.
pub fn slice_count(data: &[u8]) -> usize {
    nal_units(data).into_iter().filter(|(t, _)| *t == 1 || *t == 5).count()
}
