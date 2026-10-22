//! EGL (GBM platform, surfaceless, low-priority context) + GLES 3 colour conversion of captured
//! dmabufs: one draw packs BT.709 I420 (Y plane, then per row [U row | V row]) into an RGBA8 target
//! of (Wp/4) x (3H/2) texels, read back through a PBO (Wp*H*3/2 bytes, about 3.4 MB at 1100x2080).
//! A second mode writes BGRA for the classic bitmap path (clients without EGFX).
//!
//! Unsafe: EGL/GL FFI only (rdp-native-design.md 12, exception E1).
#![allow(unsafe_code)]

use std::ffi::c_void;
use std::os::fd::{AsRawFd, OwnedFd};

use anyhow::{Context as _, Result, anyhow, bail};
use glow::HasContext as _;
use khronos_egl as egl;

pub const DRM_FORMAT_XRGB8888: u32 = 0x3432_5258;
pub const DRM_FORMAT_ARGB8888: u32 = 0x3432_5241;
pub const DRM_FORMAT_MOD_INVALID: u64 = 0x00ff_ffff_ffff_ffff;
pub const DRM_FORMAT_MOD_LINEAR: u64 = 0;

const EGL_PLATFORM_GBM_KHR: egl::Enum = 0x31D7;
const EGL_LINUX_DMA_BUF_EXT: egl::Enum = 0x3270;
const EGL_LINUX_DRM_FOURCC_EXT: usize = 0x3271;
const EGL_CONTEXT_PRIORITY_LEVEL_IMG: egl::Int = 0x3100;
const EGL_CONTEXT_PRIORITY_LOW_IMG: egl::Int = 0x3103;
const PLANE_ATTRS: [[usize; 5]; 4] = [
    // fd, offset, pitch, modifier lo, modifier hi
    [0x3272, 0x3273, 0x3274, 0x3443, 0x3444],
    [0x3275, 0x3276, 0x3277, 0x3445, 0x3446],
    [0x3278, 0x3279, 0x327A, 0x3447, 0x3448],
    [0x3440, 0x3441, 0x3442, 0x3449, 0x344A],
];
const GL_TEXTURE_EXTERNAL_OES: u32 = 0x8D65;

type EglImageTargetTexture2DOes = unsafe extern "C" fn(target: u32, image: *const c_void);
type EglQueryDmaBufModifiersExt =
    unsafe extern "C" fn(*mut c_void, egl::Int, egl::Int, *mut u64, *mut egl::Boolean, *mut egl::Int) -> egl::Boolean;

/// One plane of a dmabuf (fd owned by the caller for the image's lifetime).
pub struct Plane<'a> {
    pub fd: &'a OwnedFd,
    pub offset: u32,
    pub stride: u32,
}

/// Colour matrix of the I420 conversion. BT.709 full range is what MS-RDPEGFX AVC420 decoders
/// (FreeRDP prim_YUV, mstsc) invert; `Bt601Limited` exists only as the negative control of test G6.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Matrix {
    Bt709Full,
    Bt709Limited,
    Bt601Limited,
}

impl Matrix {
    pub fn parse(s: &str) -> Option<Self> {
        match s {
            "bt709-full" => Some(Self::Bt709Full),
            "bt709-limited" => Some(Self::Bt709Limited),
            "bt601-limited" => Some(Self::Bt601Limited),
            _ => None,
        }
    }
    /// (Kr, Kb, limited range)
    fn coeffs(self) -> (f32, f32, bool) {
        match self {
            Self::Bt709Full => (0.2126, 0.0722, false),
            Self::Bt709Limited => (0.2126, 0.0722, true),
            Self::Bt601Limited => (0.299, 0.114, true),
        }
    }
}

pub struct Imported {
    image: egl::Image,
    tex: glow::Texture,
}

struct Target {
    tex: glow::Texture,
    fbo: glow::Framebuffer,
    pbo: glow::Buffer,
    w: i32,
    h: i32,
}

pub struct Gpu {
    egl: egl::Instance<egl::Static>,
    display: egl::Display,
    context: egl::Context,
    gl: glow::Context,
    target_tex: EglImageTargetTexture2DOes,
    query_mods: Option<EglQueryDmaBufModifiersExt>,
    program: glow::Program,
    vao: glow::VertexArray,
    targets: Vec<(u32, Target)>, // keyed by mode (0 = I420, 1 = BGRA)
    pub low_priority: bool,
    pub renderer: String,
    matrix: Matrix,
}

const VS: &str = r#"#version 300 es
void main() {
    vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));
    gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}
"#;

// gl_FragCoord.y is the memory row glReadPixels returns first-to-last, and a dmabuf's texture
// row 0 is its first scanline in memory, so no flip is needed anywhere.
const FS: &str = r#"#version 300 es
#extension GL_OES_EGL_image_external_essl3 : require
precision highp float;
uniform samplerExternalOES tex;
uniform vec2 src;      // real capture size W, H
uniform float hgt;     // H (luma rows)
uniform float wp;      // padded row width in bytes (multiple of 16)
uniform int mode;      // 0 = I420 pack, 1 = BGRA
uniform vec3 kr_kb_lim; // Kr, Kb, limited (1.0) / full (0.0)
out vec4 o;
vec3 at(float x, float y) {
    vec2 p = (vec2(min(x, src.x - 1.0), min(y, src.y - 1.0)) + 0.5) / src;
    return texture(tex, p).rgb;
}
// centre of the 2x2 block at (x, y): bilinear sampling averages the four texels
vec3 avg2(float x, float y) {
    vec2 p = (vec2(min(x, src.x - 2.0), min(y, src.y - 2.0)) + 1.0) / src;
    return texture(tex, p).rgb;
}
float Y(vec3 c) {
    float kr = kr_kb_lim.x, kb = kr_kb_lim.y, kg = 1.0 - kr - kb;
    float y = kr * c.r + kg * c.g + kb * c.b;
    return kr_kb_lim.z > 0.5 ? (16.0 + 219.0 * y) / 255.0 : y;
}
vec2 UV(vec3 c) {
    float kr = kr_kb_lim.x, kb = kr_kb_lim.y, kg = 1.0 - kr - kb;
    float y = kr * c.r + kg * c.g + kb * c.b;
    float u = (c.b - y) / (2.0 * (1.0 - kb));
    float v = (c.r - y) / (2.0 * (1.0 - kr));
    return kr_kb_lim.z > 0.5 ? (128.0 + 224.0 * vec2(u, v)) / 255.0 : vec2(u, v) + 128.0 / 255.0;
}
void main() {
    float fx = floor(gl_FragCoord.x);
    float fy = floor(gl_FragCoord.y);
    if (mode == 1) {
        vec3 c = at(fx, fy);
        o = vec4(c.b, c.g, c.r, 1.0);
        return;
    }
    if (fy < hgt) {
        float x = fx * 4.0;
        o = vec4(Y(at(x, fy)), Y(at(x + 1.0, fy)), Y(at(x + 2.0, fy)), Y(at(x + 3.0, fy)));
    } else {
        float cy = (fy - hgt) * 2.0;
        float half_w = wp / 8.0;
        bool v = fx >= half_w;
        float x = (v ? fx - half_w : fx) * 8.0;
        vec2 a = UV(avg2(x, cy));
        vec2 b = UV(avg2(x + 2.0, cy));
        vec2 c = UV(avg2(x + 4.0, cy));
        vec2 d = UV(avg2(x + 6.0, cy));
        o = v ? vec4(a.y, b.y, c.y, d.y) : vec4(a.x, b.x, c.x, d.x);
    }
}
"#;

impl Gpu {
    /// `gbm_device` is the raw `struct gbm_device *` the capture buffers are allocated from.
    pub fn new(gbm_device: *mut c_void, matrix: Matrix, want_low: bool) -> Result<Self> {
        let egl = egl::Instance::new(egl::Static);
        // SAFETY: gbm_device is a live gbm_device for the lifetime of the capture thread.
        let display = unsafe { egl.get_platform_display(EGL_PLATFORM_GBM_KHR, gbm_device, &[egl::ATTRIB_NONE]) }
            .context("eglGetPlatformDisplay(GBM)")?;
        egl.initialize(display).context("eglInitialize")?;
        let exts = egl.query_string(Some(display), egl::EXTENSIONS)?.to_string_lossy().into_owned();
        for need in ["EGL_EXT_image_dma_buf_import", "EGL_KHR_surfaceless_context"] {
            if !exts.split(' ').any(|e| e == need) {
                bail!("EGL lacks {need}");
            }
        }
        egl.bind_api(egl::OPENGL_ES_API)?;
        let config = egl
            .choose_first_config(display, &[egl::RENDERABLE_TYPE, egl::OPENGL_ES3_BIT, egl::NONE])?
            .ok_or_else(|| anyhow!("no EGL config with GLES3"))?;
        // [remote.rdp.native].gpu_priority: "low" yields to Hyprland on a busy iGPU, "normal" lets the
        // conversion interleave with the compositor's frames
        let low = want_low && exts.split(' ').any(|e| e == "EGL_IMG_context_priority");
        let mut attrs = vec![egl::CONTEXT_MAJOR_VERSION, 3, egl::CONTEXT_MINOR_VERSION, 0];
        if low {
            // Hyprland's rendering always wins on the 2-CU iGPU.
            attrs.extend([EGL_CONTEXT_PRIORITY_LEVEL_IMG, EGL_CONTEXT_PRIORITY_LOW_IMG]);
        }
        attrs.push(egl::NONE);
        let context = egl.create_context(display, config, None, &attrs).context("eglCreateContext")?;
        egl.make_current(display, None, None, Some(context)).context("eglMakeCurrent")?;
        let low_priority = low
            && egl.query_context(display, context, EGL_CONTEXT_PRIORITY_LEVEL_IMG).unwrap_or(0)
                == EGL_CONTEXT_PRIORITY_LOW_IMG;
        // SAFETY: the loader returns GL entry points of the current context.
        let gl = unsafe {
            glow::Context::from_loader_function(|s| {
                egl.get_proc_address(s).map_or(std::ptr::null(), |p| p as *const c_void)
            })
        };
        let target_tex = egl
            .get_proc_address("glEGLImageTargetTexture2DOES")
            .ok_or_else(|| anyhow!("no glEGLImageTargetTexture2DOES"))?;
        // SAFETY: the symbol has this signature (OES_EGL_image).
        let target_tex: EglImageTargetTexture2DOes = unsafe { std::mem::transmute(target_tex) };
        let query_mods = egl
            .get_proc_address("eglQueryDmaBufModifiersEXT")
            // SAFETY: EGL_EXT_image_dma_buf_import_modifiers signature.
            .map(|p| unsafe { std::mem::transmute::<extern "system" fn(), EglQueryDmaBufModifiersExt>(p) });
        // SAFETY: GL calls on the current context.
        let renderer = unsafe { gl.get_parameter_string(glow::RENDERER) };
        let gl_exts = unsafe { gl.get_parameter_string(glow::EXTENSIONS) };
        if !gl_exts.contains("GL_OES_EGL_image_external_essl3") {
            bail!("GLES lacks GL_OES_EGL_image_external_essl3 ({renderer})");
        }
        let (program, vao) = unsafe { build_program(&gl)? };
        Ok(Self {
            egl,
            display,
            context,
            gl,
            target_tex,
            query_mods,
            program,
            vao,
            targets: Vec::new(),
            low_priority,
            renderer,
            matrix,
        })
    }

    /// Modifiers EGL can import for `format`: (modifier, external_only).
    pub fn importable_modifiers(&self, format: u32) -> Vec<(u64, bool)> {
        let Some(q) = self.query_mods else { return Vec::new() };
        let fmt = format as egl::Int;
        let mut n: egl::Int = 0;
        // SAFETY: two-call pattern of eglQueryDmaBufModifiersEXT.
        unsafe {
            if q(self.display.as_ptr(), fmt, 0, std::ptr::null_mut(), std::ptr::null_mut(), &mut n) == egl::FALSE
                || n <= 0
            {
                return Vec::new();
            }
            let mut mods = vec![0u64; n as usize];
            let mut ext = vec![0 as egl::Boolean; n as usize];
            if q(self.display.as_ptr(), fmt, n, mods.as_mut_ptr(), ext.as_mut_ptr(), &mut n) == egl::FALSE {
                return Vec::new();
            }
            mods.into_iter().zip(ext).take(n as usize).map(|(m, e)| (m, e != egl::FALSE)).collect()
        }
    }

    pub fn import(&self, width: u32, height: u32, format: u32, modifier: u64, planes: &[Plane<'_>]) -> Result<Imported> {
        if planes.is_empty() || planes.len() > 4 {
            bail!("dmabuf with {} planes", planes.len());
        }
        let mut a: Vec<usize> = vec![
            egl::WIDTH as usize,
            width as usize,
            egl::HEIGHT as usize,
            height as usize,
            EGL_LINUX_DRM_FOURCC_EXT,
            format as usize,
        ];
        for (i, p) in planes.iter().enumerate() {
            let k = PLANE_ATTRS[i];
            a.extend([k[0], p.fd.as_raw_fd() as usize, k[1], p.offset as usize, k[2], p.stride as usize]);
            if modifier != DRM_FORMAT_MOD_INVALID {
                a.extend([k[3], (modifier & 0xffff_ffff) as usize, k[4], (modifier >> 32) as usize]);
            }
        }
        a.push(egl::ATTRIB_NONE);
        // SAFETY: EGL_NO_CONTEXT + null client buffer is the dmabuf import contract.
        let image = self
            .egl
            .create_image(
                self.display,
                unsafe { egl::Context::from_ptr(egl::NO_CONTEXT) },
                EGL_LINUX_DMA_BUF_EXT,
                unsafe { egl::ClientBuffer::from_ptr(std::ptr::null_mut()) },
                &a,
            )
            .context("eglCreateImage(dmabuf)")?;
        // SAFETY: GL calls on the current context; the image outlives the texture binding.
        unsafe {
            let tex = self.gl.create_texture().map_err(|e| anyhow!(e))?;
            self.gl.bind_texture(GL_TEXTURE_EXTERNAL_OES, Some(tex));
            self.gl.tex_parameter_i32(GL_TEXTURE_EXTERNAL_OES, glow::TEXTURE_MIN_FILTER, glow::LINEAR as i32);
            self.gl.tex_parameter_i32(GL_TEXTURE_EXTERNAL_OES, glow::TEXTURE_MAG_FILTER, glow::LINEAR as i32);
            self.gl.tex_parameter_i32(GL_TEXTURE_EXTERNAL_OES, glow::TEXTURE_WRAP_S, glow::CLAMP_TO_EDGE as i32);
            self.gl.tex_parameter_i32(GL_TEXTURE_EXTERNAL_OES, glow::TEXTURE_WRAP_T, glow::CLAMP_TO_EDGE as i32);
            (self.target_tex)(GL_TEXTURE_EXTERNAL_OES, image.as_ptr());
            if self.gl.get_error() != glow::NO_ERROR {
                self.gl.delete_texture(tex);
                let _ = self.egl.destroy_image(self.display, image);
                bail!("glEGLImageTargetTexture2DOES failed");
            }
            self.gl.bind_texture(GL_TEXTURE_EXTERNAL_OES, None);
            Ok(Imported { image, tex })
        }
    }

    pub fn release(&self, img: Imported) {
        // SAFETY: objects created by this context.
        unsafe { self.gl.delete_texture(img.tex) };
        let _ = self.egl.destroy_image(self.display, img.image);
    }

    fn target(&mut self, mode: u32, w: i32, h: i32) -> Result<usize> {
        if let Some(i) = self.targets.iter().position(|(m, t)| *m == mode && t.w == w && t.h == h) {
            return Ok(i);
        }
        if let Some(i) = self.targets.iter().position(|(m, _)| *m == mode) {
            let (_, t) = self.targets.remove(i);
            // SAFETY: own objects.
            unsafe {
                self.gl.delete_framebuffer(t.fbo);
                self.gl.delete_texture(t.tex);
                self.gl.delete_buffer(t.pbo);
            }
        }
        // SAFETY: GL object creation on the current context.
        unsafe {
            let gl = &self.gl;
            let tex = gl.create_texture().map_err(|e| anyhow!(e))?;
            gl.bind_texture(glow::TEXTURE_2D, Some(tex));
            gl.tex_storage_2d(glow::TEXTURE_2D, 1, glow::RGBA8, w, h);
            let fbo = gl.create_framebuffer().map_err(|e| anyhow!(e))?;
            gl.bind_framebuffer(glow::FRAMEBUFFER, Some(fbo));
            gl.framebuffer_texture_2d(glow::FRAMEBUFFER, glow::COLOR_ATTACHMENT0, glow::TEXTURE_2D, Some(tex), 0);
            if gl.check_framebuffer_status(glow::FRAMEBUFFER) != glow::FRAMEBUFFER_COMPLETE {
                bail!("conversion framebuffer incomplete");
            }
            let pbo = gl.create_buffer().map_err(|e| anyhow!(e))?;
            gl.bind_buffer(glow::PIXEL_PACK_BUFFER, Some(pbo));
            gl.buffer_data_size(glow::PIXEL_PACK_BUFFER, w * h * 4, glow::STREAM_READ);
            gl.bind_buffer(glow::PIXEL_PACK_BUFFER, None);
            self.targets.push((mode, Target { tex, fbo, pbo, w, h }));
        }
        Ok(self.targets.len() - 1)
    }

    /// Convert `img` (W x H) to I420 with row stride `wp` (multiple of 16, >= W) into `out`
    /// (resized to wp*H*3/2): Y rows, then H/2 rows of [U (wp/2 bytes) | V (wp/2 bytes)].
    /// Returns the GPU wait in microseconds.
    pub fn convert_i420(&mut self, img: &Imported, w: u32, h: u32, wp: u32, out: &mut Vec<u8>) -> Result<u64> {
        if h % 2 != 0 || wp % 16 != 0 || wp < w {
            bail!("bad I420 geometry {w}x{h} stride {wp}");
        }
        let tw = (wp / 4) as i32;
        let th = (h + h / 2) as i32;
        self.run(0, img, w, h, wp, tw, th, out)
    }

    /// BGRA (B,G,R,A bytes) W x H, stride W*4.
    pub fn convert_bgra(&mut self, img: &Imported, w: u32, h: u32, out: &mut Vec<u8>) -> Result<u64> {
        self.run(1, img, w, h, w * 4, w as i32, h as i32, out)
    }

    #[expect(clippy::too_many_arguments)]
    fn run(&mut self, mode: u32, img: &Imported, w: u32, h: u32, wp: u32, tw: i32, th: i32, out: &mut Vec<u8>) -> Result<u64> {
        let ti = self.target(mode, tw, th)?;
        let (kr, kb, lim) = self.matrix.coeffs();
        let bytes = (tw * th * 4) as usize;
        out.resize(bytes, 0);
        let t = &self.targets[ti].1;
        // SAFETY: GL calls on the current context with objects owned by self.
        unsafe {
            let gl = &self.gl;
            gl.bind_framebuffer(glow::FRAMEBUFFER, Some(t.fbo));
            gl.viewport(0, 0, tw, th);
            gl.use_program(Some(self.program));
            gl.bind_vertex_array(Some(self.vao));
            gl.active_texture(glow::TEXTURE0);
            gl.bind_texture(GL_TEXTURE_EXTERNAL_OES, Some(img.tex));
            let loc = |n: &str| gl.get_uniform_location(self.program, n);
            gl.uniform_1_i32(loc("tex").as_ref(), 0);
            gl.uniform_2_f32(loc("src").as_ref(), w as f32, h as f32);
            gl.uniform_1_f32(loc("hgt").as_ref(), h as f32);
            gl.uniform_1_f32(loc("wp").as_ref(), wp as f32);
            gl.uniform_1_i32(loc("mode").as_ref(), mode as i32);
            gl.uniform_3_f32(loc("kr_kb_lim").as_ref(), kr, kb, if lim { 1.0 } else { 0.0 });
            gl.draw_arrays(glow::TRIANGLES, 0, 3);
            gl.bind_buffer(glow::PIXEL_PACK_BUFFER, Some(t.pbo));
            gl.pixel_store_i32(glow::PACK_ALIGNMENT, 4);
            gl.read_pixels(0, 0, tw, th, glow::RGBA, glow::UNSIGNED_BYTE, glow::PixelPackData::BufferOffset(0));
            let fence = gl.fence_sync(glow::SYNC_GPU_COMMANDS_COMPLETE, 0).map_err(|e| anyhow!(e))?;
            let t0 = std::time::Instant::now();
            let r = gl.client_wait_sync(fence, glow::SYNC_FLUSH_COMMANDS_BIT, 500_000_000);
            gl.delete_sync(fence);
            let waited = t0.elapsed().as_micros() as u64;
            if r != glow::ALREADY_SIGNALED && r != glow::CONDITION_SATISFIED {
                gl.bind_buffer(glow::PIXEL_PACK_BUFFER, None);
                bail!("GPU conversion did not finish within 500 ms");
            }
            let p = gl.map_buffer_range(glow::PIXEL_PACK_BUFFER, 0, bytes as i32, glow::MAP_READ_BIT);
            if p.is_null() {
                gl.bind_buffer(glow::PIXEL_PACK_BUFFER, None);
                bail!("glMapBufferRange failed");
            }
            std::ptr::copy_nonoverlapping(p, out.as_mut_ptr(), bytes);
            gl.unmap_buffer(glow::PIXEL_PACK_BUFFER);
            gl.bind_buffer(glow::PIXEL_PACK_BUFFER, None);
            gl.bind_texture(GL_TEXTURE_EXTERNAL_OES, None);
            Ok(waited)
        }
    }
}

impl Drop for Gpu {
    fn drop(&mut self) {
        // SAFETY: own objects on the current context.
        unsafe {
            for (_, t) in self.targets.drain(..) {
                self.gl.delete_framebuffer(t.fbo);
                self.gl.delete_texture(t.tex);
                self.gl.delete_buffer(t.pbo);
            }
            self.gl.delete_program(self.program);
            self.gl.delete_vertex_array(self.vao);
        }
        let _ = self.egl.make_current(self.display, None, None, None);
        let _ = self.egl.destroy_context(self.display, self.context);
        let _ = self.egl.terminate(self.display);
    }
}

unsafe fn build_program(gl: &glow::Context) -> Result<(glow::Program, glow::VertexArray)> {
    // SAFETY: caller holds a current context.
    unsafe {
        let program = gl.create_program().map_err(|e| anyhow!(e))?;
        let mut shaders = Vec::new();
        for (kind, src) in [(glow::VERTEX_SHADER, VS), (glow::FRAGMENT_SHADER, FS)] {
            let s = gl.create_shader(kind).map_err(|e| anyhow!(e))?;
            gl.shader_source(s, src);
            gl.compile_shader(s);
            if !gl.get_shader_compile_status(s) {
                bail!("shader compile: {}", gl.get_shader_info_log(s));
            }
            gl.attach_shader(program, s);
            shaders.push(s);
        }
        gl.link_program(program);
        if !gl.get_program_link_status(program) {
            bail!("program link: {}", gl.get_program_info_log(program));
        }
        for s in shaders {
            gl.detach_shader(program, s);
            gl.delete_shader(s);
        }
        let vao = gl.create_vertex_array().map_err(|e| anyhow!(e))?;
        Ok((program, vao))
    }
}
