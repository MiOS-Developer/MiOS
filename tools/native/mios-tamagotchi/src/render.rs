// AI-hint: Signed camera projection samples only the viewport; indexed packing retains terminal color semantics.
// AI-related: sprite.rs, capabilities.rs, ratatui::symbols::pixel::QUADRANTS
use ratatui::{
    buffer::Buffer,
    layout::Rect,
    style::Color,
    widgets::{Paragraph, Widget},
};

use crate::{
    capabilities::{Depth, Renderer},
    config::{parse_rgb, Config},
    sprite::{cell_size, fit_scale, Sprite, SpriteStyle},
};

#[derive(Clone, Debug)]
pub struct Colors {
    pub palette: Vec<Color>,
    pub background: Color,
    pub lighting: Option<crate::lighting::VoxelLighting>,
}

impl Colors {
    pub fn ink(&self, index: usize) -> Color {
        self.palette
            .get(index)
            .copied()
            .or_else(|| self.palette.get(index % self.palette.len().max(1)).copied())
            .unwrap_or(Color::Reset)
    }
    pub fn from_config(config: &Config, depth: Depth) -> Result<Self, String> {
        config.validate()?;
        let mut palette = vec![Color::Reset; config.palette.len()];
        for (dest, text) in palette.iter_mut().zip(&config.palette) {
            *dest = quantize(parse_rgb(text)?, depth);
        }
        Ok(Self {
            palette,
            background: quantize(parse_rgb(&config.background)?, depth),
            lighting: Some(config.voxel_lighting.clone()),
        })
    }
}

pub fn quantize(rgb: [u8; 3], depth: Depth) -> Color {
    // Nominal ANSI protocol colors, not application theme defaults.
    const ANSI: [([u8; 3], Color); 16] = [
        ([0, 0, 0], Color::Black),
        ([128, 0, 0], Color::Red),
        ([0, 128, 0], Color::Green),
        ([128, 128, 0], Color::Yellow),
        ([0, 0, 128], Color::Blue),
        ([128, 0, 128], Color::Magenta),
        ([0, 128, 128], Color::Cyan),
        ([192, 192, 192], Color::Gray),
        ([128, 128, 128], Color::DarkGray),
        ([255, 0, 0], Color::LightRed),
        ([0, 255, 0], Color::LightGreen),
        ([255, 255, 0], Color::LightYellow),
        ([0, 0, 255], Color::LightBlue),
        ([255, 0, 255], Color::LightMagenta),
        ([0, 255, 255], Color::LightCyan),
        ([255, 255, 255], Color::White),
    ];
    let distance = |other: [u8; 3]| -> i32 {
        rgb.iter()
            .zip(other)
            .map(|(&a, b)| (i32::from(a) - i32::from(b)).pow(2))
            .sum()
    };
    match depth {
        Depth::Mono => Color::Reset,
        Depth::Rgb => Color::Rgb(rgb[0], rgb[1], rgb[2]),
        Depth::Ansi => ANSI
            .iter()
            .min_by_key(|(c, _)| distance(*c))
            .map_or(Color::Reset, |(_, c)| *c),
        Depth::Indexed => {
            let levels = [0, 95, 135, 175, 215, 255];
            let mut best = (i32::MAX, 16u8);
            for r in 0..6 {
                for g in 0..6 {
                    for b in 0..6 {
                        let candidate = (
                            distance([levels[r], levels[g], levels[b]]),
                            (16 + 36 * r + 6 * g + b) as u8,
                        );
                        if candidate.0 < best.0 {
                            best = candidate;
                        }
                    }
                }
            }
            for i in 0..24 {
                let level = 8 + 10 * i;
                let candidate = (distance([level; 3]), 232 + i);
                if candidate.0 < best.0 {
                    best = candidate;
                }
            }
            Color::Indexed(best.1)
        }
    }
}

pub fn quadrant(pixels: [bool; 4]) -> char {
    let index = pixels
        .iter()
        .enumerate()
        .fold(0, |mask, (bit, &on)| mask | ((on as usize) << bit));
    ratatui::symbols::pixel::QUADRANTS[index]
}

pub fn braille(pixels: [bool; 8]) -> char {
    let mask = pixels
        .iter()
        .zip([0, 3, 1, 4, 2, 5, 6, 7])
        .fold(0u32, |mask, (&on, bit)| mask | ((on as u32) << bit));
    char::from_u32(0x2800 + mask).unwrap_or(' ')
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, serde::Deserialize, clap::ValueEnum)]
#[serde(rename_all = "kebab-case")]
#[value(rename_all = "kebab-case")]
pub enum CameraView {
    Room,
    Pet,
    Face,
}

/// Presentation only; camera interpolation never changes pet simulation.
#[derive(Clone, Copy, Debug)]
pub struct Camera {
    /// World azimuth; elevation remains fixed to the isometric SSOT angle.
    pub orbit_degrees: u16,
    pub view: CameraView,
    pub zoom_percent: u16,
    pub from_view: CameraView,
    pub transition_percent: u16,
}

impl Default for Camera {
    fn default() -> Self {
        Self {
            orbit_degrees: 45,
            view: CameraView::Pet,
            zoom_percent: 100,
            from_view: CameraView::Pet,
            transition_percent: 100,
        }
    }
}

#[derive(Clone, Copy, Debug)]
struct SolidBounds {
    left: f64,
    top: f64,
    right: f64,
    bottom: f64,
}

impl SolidBounds {
    fn of(sprite: &Sprite) -> Option<Self> {
        let mut bounds = Self {
            left: f64::from(sprite.width()),
            top: f64::from(sprite.height()),
            right: 0.0,
            bottom: 0.0,
        };
        for y in 0..sprite.height() {
            for x in 0..sprite.width() {
                if sprite.pixel(u32::from(x), u32::from(y), 1).is_some() {
                    bounds.left = bounds.left.min(f64::from(x));
                    bounds.top = bounds.top.min(f64::from(y));
                    bounds.right = bounds.right.max(f64::from(x) + 1.0);
                    bounds.bottom = bounds.bottom.max(f64::from(y) + 1.0);
                }
            }
        }
        (bounds.right > bounds.left && bounds.bottom > bounds.top).then_some(bounds)
    }

    fn width(self) -> f64 {
        self.right - self.left
    }

    fn height(self) -> f64 {
        self.bottom - self.top
    }
}

/// Signed origin and scale in packed screen pixels. Nothing proportional to
/// the zoomed sprite is allocated: only visible terminal samples are visited.
#[derive(Clone, Copy, Debug)]
struct Projection {
    left: f64,
    top: f64,
    scale: f64,
    scale_y: f64,
}

/// Current terminal capability/config contracts expose cells, not physical
/// font metrics. Match the conventional 1:2 cell geometry used by half-blocks.
fn camera_pixel_aspect(renderer: Renderer) -> f64 {
    let (dx, dy) = renderer.packing();
    f64::from(dy) / (2.0 * f64::from(dx))
}

/// Camera magnification range, in percent of the fitted frame.
const MIN_ZOOM_PERCENT: u16 = 50;
const MAX_ZOOM_PERCENT: u16 = 400;

/// The face plane's centre, where the kaomoji is laid out: the mapped face
/// texel whose UV is nearest (0.5, 0.5), as a source-pixel point. Eggs, legacy
/// pixel sprites and rear views map no face and have no anchor.
fn face_anchor(sprite: &Sprite) -> Option<(f64, f64)> {
    let mut best: Option<(f64, (f64, f64))> = None;
    for y in 0..u32::from(sprite.height()) {
        for x in 0..u32::from(sprite.width()) {
            if let Some(uv) = valid_face_uv(sprite, x, y) {
                let distance = (uv[0] - 0.5).powi(2) + (uv[1] - 0.5).powi(2);
                if best.is_none_or(|(nearest, _)| distance < nearest) {
                    best = Some((distance, (f64::from(x) + 0.5, f64::from(y) + 0.5)));
                }
            }
        }
    }
    best.map(|(_, point)| point)
}

impl Projection {
    fn camera(
        sprite: &Sprite,
        bounds: SolidBounds,
        area: Rect,
        renderer: Renderer,
        fixed_scale: u16,
        position: (u16, u16),
        camera: Camera,
    ) -> Self {
        let (dx, dy) = renderer.packing();
        let pixel_aspect = camera_pixel_aspect(renderer);
        let width = f64::from(area.width) * f64::from(dx);
        let height = f64::from(area.height) * f64::from(dy);
        let zoom = f64::from(
            camera
                .zoom_percent
                .clamp(MIN_ZOOM_PERCENT, MAX_ZOOM_PERCENT),
        ) / 100.0;
        let max_zoom = f64::from(MAX_ZOOM_PERCENT) / 100.0;
        let anchor = face_anchor(sprite);
        let mode = |view| {
            let mut frame = bounds;
            if view == CameraView::Face {
                let face = sprite.face_overlay();
                let cx = face.map_or((bounds.left + bounds.right) / 2.0, |f| {
                    f64::from(f.center_x) + 0.5
                });
                let cy = face.map_or(bounds.top + bounds.height() * 0.28, |f| {
                    f64::from(f.y) + 0.5
                });
                if let Some(head) = head_surface_bounds(sprite) {
                    // The close-up belongs to the actual visible head. A wide
                    // tail or a tall torso must never make its face smaller.
                    // Keep the ears above the head without fitting the body.
                    let padding_x = head.width() * 0.15;
                    let padding_y = head.height() * 0.10;
                    frame = SolidBounds {
                        left: head.left - padding_x,
                        right: head.right + padding_x,
                        top: bounds.top.min(head.top - padding_y),
                        bottom: (head.bottom + padding_y).min(bounds.bottom),
                    };
                } else {
                    // Legacy pixels, eggs and rear silhouettes may have no
                    // mapped head surface. Their upper silhouette stays usable
                    // without manufacturing a face or changing occlusion.
                    frame.left = cx - bounds.width() * 0.55;
                    frame.right = cx + bounds.width() * 0.55;
                    frame.top = bounds.top - bounds.height() * 0.025;
                    frame.bottom = (cy + bounds.height() * 0.18)
                        .max(bounds.top + bounds.height() * 0.38)
                        .min(bounds.bottom);
                }
            }
            let fit = (width / frame.width()).min(height / (frame.height() * pixel_aspect));
            let base = if fixed_scale == 0 {
                fit * if view == CameraView::Room { 0.6 } else { 0.96 }
            } else {
                f64::from(fixed_scale)
                    * if view == CameraView::Face {
                        bounds.height() / frame.height()
                    } else {
                        1.0
                    }
            };
            let mut scale = base.max(f64::EPSILON) * zoom;
            let micro_face = sprite.face_overlay().filter(|face| {
                view == CameraView::Face
                    && area.height <= 3
                    && usize::from(area.width) >= face.text.len()
                    && printable_face(face.text)
            });
            if let Some(face) = micro_face {
                let (head_left, head_right) = head_span(sprite, face.center_x, face.y);
                let cx = f64::from(face.center_x) + 0.5;
                let half_text = (face.text.len() as f64 / 2.0 + 1.0) * f64::from(dx);
                // A micro close-up crops around the face itself. At two rows,
                // whole-ear fitting would make five literal glyphs impossible.
                let readable = (half_text / (cx - head_left).max(0.5))
                    .max(half_text / (head_right - cx).max(0.5));
                scale = base.max(readable) * zoom;
                frame.left = cx - bounds.width() / 2.0;
                frame.right = cx + bounds.width() / 2.0;
                let cy = f64::from(face.y) + 0.5;
                frame.top = cy - bounds.height() / 2.0;
                frame.bottom = cy + bounds.height() / 2.0;
            }
            let scale_y = scale * pixel_aspect;
            let centre = (
                (frame.left + frame.right) / 2.0,
                (frame.top + frame.bottom) / 2.0,
            );
            // Magnifying the close-up zooms into the face, not the ears-to-chin
            // frame: the anchor keeps the screen point it has at 100% and eases
            // to the viewport centre by the maximum zoom, so the expression
            // never slides out of view. Zooming out keeps the fitted framing.
            let face_pivot =
                anchor.filter(|_| view == CameraView::Face && micro_face.is_none() && zoom > 1.0);
            let (left, top) = if let Some((ax, ay)) = face_pivot {
                let fitted = base.max(f64::EPSILON);
                let ease = ((zoom - 1.0) / (max_zoom - 1.0)).clamp(0.0, 1.0);
                let x = width / 2.0 + (ax - centre.0) * fitted;
                let y = height / 2.0 + (ay - centre.1) * fitted * pixel_aspect;
                (
                    x + (width / 2.0 - x) * ease - ax * scale,
                    y + (height / 2.0 - y) * ease - ay * scale_y,
                )
            } else if view == CameraView::Room {
                (
                    (width - bounds.width() * scale) * f64::from(position.0.min(1000)) / 1000.0
                        - bounds.left * scale,
                    (height - bounds.height() * scale_y) * f64::from(position.1.min(1000)) / 1000.0
                        - bounds.top * scale_y,
                )
            } else {
                (
                    width / 2.0 - centre.0 * scale,
                    height / 2.0 - centre.1 * scale_y,
                )
            };
            Self {
                left: f64::from(area.x) * f64::from(dx) + left,
                top: f64::from(area.y) * f64::from(dy) + top,
                scale,
                scale_y,
            }
        };
        let from = mode(camera.from_view);
        let to = mode(camera.view);
        let blend = f64::from(camera.transition_percent.min(100)) / 100.0;
        Self {
            left: from.left + (to.left - from.left) * blend,
            top: from.top + (to.top - from.top) * blend,
            scale: from.scale + (to.scale - from.scale) * blend,
            scale_y: from.scale_y + (to.scale_y - from.scale_y) * blend,
        }
    }

    fn source_pixel(self, sprite: &Sprite, x: u32, y: u32) -> Option<(u32, u32)> {
        let x = ((f64::from(x) + 0.5 - self.left) / self.scale).floor();
        let y = ((f64::from(y) + 0.5 - self.top) / self.scale_y).floor();
        if x < 0.0 || y < 0.0 || x >= f64::from(sprite.width()) || y >= f64::from(sprite.height()) {
            None
        } else {
            Some((x as u32, y as u32))
        }
    }

    fn sample(self, sprite: &Sprite, x: u32, y: u32) -> Option<u8> {
        let (x, y) = self.source_pixel(sprite, x, y)?;
        sprite.pixel(x, y, 1)
    }

    fn cell(self, x: f64, y: f64, renderer: Renderer) -> (f64, f64) {
        let (dx, dy) = renderer.packing();
        (
            (self.left + x * self.scale) / f64::from(dx),
            (self.top + y * self.scale_y) / f64::from(dy),
        )
    }
}

fn paint_pixels(
    renderer: Renderer,
    colors: &Colors,
    pixels: [Option<u8>; 8],
    cell: &mut ratatui::buffer::Cell,
) {
    let Some(index) = pixels.iter().flatten().next() else {
        return;
    };
    let mut fg = colors.ink(usize::from(*index));
    let mut bg = if cell.bg == Color::Reset {
        colors.background
    } else {
        cell.bg
    };
    let symbol = match renderer {
        Renderer::Auto | Renderer::Ascii => ['#', 'o', '@', '*'][usize::from(*index) % 4],
        Renderer::HalfBlock if colors.palette.iter().all(|c| *c == Color::Reset) => {
            ['#', 'o', '@', '*'][usize::from(*index) % 4]
        }
        Renderer::HalfBlock => match (pixels[0], pixels[1]) {
            (Some(upper), Some(lower)) => {
                fg = colors.ink(usize::from(upper));
                bg = colors.ink(usize::from(lower));
                if upper == lower {
                    '█'
                } else {
                    '▀'
                }
            }
            (Some(_), None) => '▀',
            (None, Some(_)) => '▄',
            _ => ' ',
        },
        Renderer::Quadrant => quadrant(std::array::from_fn(|i| pixels[i].is_some())),
        Renderer::Braille => braille(std::array::from_fn(|i| pixels[i].is_some())),
    };
    cell.set_char(symbol).set_fg(fg).set_bg(bg);
}

/// Shading belongs to the exact winning surface sampled by the camera.
#[derive(Clone, Copy, Debug)]
struct SurfaceLight {
    normal: [f32; 3],
    occlusion: u8,
    edge: bool,
    /// The visible silhouette ends beside this packed pixel on screen, at
    /// whatever magnification the camera chose (source edges alone thicken
    /// when zoomed in and vanish when the Mon is shrunk).
    screen_edge: bool,
    /// Source pixel inside one uniform material, away from the head that
    /// carries the face and from small features (eyes, noses, blush): only
    /// these samples may carry the ASCII shading texture.
    texture: Option<(u32, u32)>,
}

fn projected_surface_light(
    sprite: &Sprite,
    projection: Projection,
    x: u32,
    y: u32,
) -> Option<SurfaceLight> {
    let (sx, sy) = projection.source_pixel(sprite, x, y)?;
    let (normal, occlusion) = sprite.surface_shading(sx, sy)?;
    let edge = sx
        .checked_sub(1)
        .and_then(|x| sprite.pixel(x, sy, 1))
        .is_none()
        || sprite.pixel(sx + 1, sy, 1).is_none()
        || sy
            .checked_sub(1)
            .and_then(|y| sprite.pixel(sx, y, 1))
            .is_none()
        || sprite.pixel(sx, sy + 1, 1).is_none();
    // Screen neighbours before the buffer origin are cropped, not empty.
    let empty_on_screen = |px: Option<u32>, py: Option<u32>| {
        px.zip(py)
            .is_some_and(|(px, py)| projection.sample(sprite, px, py).is_none())
    };
    let screen_edge = empty_on_screen(x.checked_sub(1), Some(y))
        || empty_on_screen(x.checked_add(1), Some(y))
        || empty_on_screen(Some(x), y.checked_sub(1))
        || empty_on_screen(Some(x), y.checked_add(1));
    let index = sprite.pixel(sx, sy, 1);
    let same_material = |nx: Option<u32>, ny: Option<u32>| {
        nx.zip(ny)
            .is_some_and(|(nx, ny)| sprite.pixel(nx, ny, 1) == index)
    };
    let interior = same_material(sx.checked_sub(1), Some(sy))
        && same_material(sx.checked_add(1), Some(sy))
        && same_material(Some(sx), sy.checked_sub(1))
        && same_material(Some(sx), sy.checked_add(1));
    let texture = (interior && sprite.head_uv(sx, sy).is_none()).then_some((sx, sy));
    Some(SurfaceLight {
        normal,
        occlusion,
        edge: edge || screen_edge,
        screen_edge,
        texture,
    })
}

/// Perceived luminance0..=255 of a terminal color; None for the terminal's
/// own default color, whose actual value is unknown.
fn luminance(color: Color) -> Option<u32> {
    terminal_rgb(color)
        .map(|[r, g, b]| (299 * u32::from(r) + 587 * u32::from(g) + 114 * u32::from(b)) / 1000)
}

/// The configured outline strength as a luma separation, 0..=255.
fn outline_separation(lighting: &crate::lighting::VoxelLighting) -> u32 {
    u32::from(lighting.outline_percent.min(100)) * 255 / 100
}

/// Keep a silhouette sample distinct from the scene behind it. Bounded: only
/// screen-edge samples reach here, and their lit color changes only when it
/// would quantize too close to the background. The required separation is the
/// configured outline strength. Candidates: outline ink, the lighting model's
/// own brightest/darkest rims, then the Mon's own palette (a 16-color console
/// can collapse a dark body and every rim of it to the scene's black); first
/// adequate one wins, else the most separated.
fn silhouette_color(
    lighting: &crate::lighting::VoxelLighting,
    colors: &Colors,
    lit: Color,
    base: Color,
    scene: Color,
) -> Color {
    let Some(scene_luma) = luminance(scene) else {
        return lit;
    };
    let separation = |color: Color| luminance(color).map_or(0, |luma| luma.abs_diff(scene_luma));
    let minimum = u32::from(lighting.outline_percent.min(100)) * 255 / 100;
    let adequate = |color: Color| color != scene && separation(color) >= minimum;
    if adequate(lit) {
        return lit;
    }
    let rims = [
        colors.ink(0),
        shade_terminal_color(lighting, base, 180),
        shade_terminal_color(lighting, base, 0),
    ];
    let candidates = || rims.into_iter().chain(colors.palette.iter().copied());
    candidates()
        .find(|color| adequate(*color))
        .or_else(|| {
            std::iter::once(lit)
                .chain(candidates())
                .max_by_key(|color| separation(*color))
        })
        .unwrap_or(lit)
}

/// Sparse ASCII shading over magnified voxels: a cell that shows one uniform
/// material surface keeps that surface as its background color and gains a
/// ramp glyph whose density follows the surface's own shadow. Lit faces stay
/// clean, so the overlay never adds coverage, edges, faces or features.
fn texture_voxel_cell(
    style: (Renderer, &crate::lighting::VoxelLighting),
    pixels: [Option<u8>; 8],
    surface: [Option<SurfaceLight>; 8],
    flat_ink: [bool; 8],
    brightness: [Option<u16>; 8],
    shaded: [Option<Color>; 8],
    cell: &mut ratatui::buffer::Cell,
) {
    let (renderer, lighting) = style;
    let (dx, dy) = renderer.packing();
    let count = usize::from(dx * dy);
    let Some(source) = surface[0].and_then(|s| s.texture) else {
        return;
    };
    let uniform = (0..count).all(|i| {
        pixels[i].is_some()
            && pixels[i] == pixels[0]
            && !flat_ink[i]
            && surface[i].is_some_and(|s| !s.screen_edge && s.texture == Some(source))
    });
    let (Some(value), Some(body)) = (brightness[0], shaded[0]) else {
        return;
    };
    let darkness = 100u16.saturating_sub(value);
    let threshold = lighting.outline_percent.min(99);
    if !uniform || darkness <= threshold {
        return;
    }
    let level = (darkness - threshold) * 180 / (100 - threshold);
    let ink = shade_terminal_color(lighting, body, value);
    cell.set_char(lighting.glyph(level))
        .set_fg(ink)
        .set_bg(body);
}

/// Canonical terminal palette decoding; shade RGB without swapping skin hues.
fn terminal_rgb(color: Color) -> Option<[u8; 3]> {
    let ansi = [
        [0, 0, 0],
        [128, 0, 0],
        [0, 128, 0],
        [128, 128, 0],
        [0, 0, 128],
        [128, 0, 128],
        [0, 128, 128],
        [192, 192, 192],
        [128, 128, 128],
        [255, 0, 0],
        [0, 255, 0],
        [255, 255, 0],
        [0, 0, 255],
        [255, 0, 255],
        [0, 255, 255],
        [255, 255, 255],
    ];
    Some(match color {
        Color::Reset => return None,
        Color::Rgb(r, g, b) => [r, g, b],
        Color::Indexed(index) => match index {
            0..=15 => ansi[usize::from(index)],
            16..=231 => {
                let n = index - 16;
                let levels = [0, 95, 135, 175, 215, 255];
                [
                    levels[usize::from(n / 36)],
                    levels[usize::from(n / 6 % 6)],
                    levels[usize::from(n % 6)],
                ]
            }
            _ => [8 + 10 * (index - 232); 3],
        },
        _ => match color {
            Color::Black => ansi[0],
            Color::Red => ansi[1],
            Color::Green => ansi[2],
            Color::Yellow => ansi[3],
            Color::Blue => ansi[4],
            Color::Magenta => ansi[5],
            Color::Cyan => ansi[6],
            Color::Gray => ansi[7],
            Color::DarkGray => ansi[8],
            Color::LightRed => ansi[9],
            Color::LightGreen => ansi[10],
            Color::LightYellow => ansi[11],
            Color::LightBlue => ansi[12],
            Color::LightMagenta => ansi[13],
            Color::LightCyan => ansi[14],
            Color::White => ansi[15],
            _ => return None,
        },
    })
}

fn shade_terminal_color(
    lighting: &crate::lighting::VoxelLighting,
    color: Color,
    brightness: u16,
) -> Color {
    let Some(rgb) = terminal_rgb(color) else {
        return color;
    };
    let rgb = lighting.shade_rgb(rgb, brightness);
    match color {
        Color::Rgb(..) => Color::Rgb(rgb[0], rgb[1], rgb[2]),
        Color::Indexed(_) => quantize(rgb, Depth::Indexed),
        _ => quantize(rgb, Depth::Ansi),
    }
}

/// Preserve the unlit packer contract; only voxels with surface metadata light.
fn paint_lit_pixels(
    renderer: Renderer,
    colors: &Colors,
    pixels: [Option<u8>; 8],
    surface: [Option<SurfaceLight>; 8],
    flat_ink: [bool; 8],
    cell: &mut ratatui::buffer::Cell,
) {
    // The scene behind this cell, before the Mon paints over it.
    let scene = if cell.bg == Color::Reset {
        colors.background
    } else {
        cell.bg
    };
    paint_pixels(renderer, colors, pixels, cell);
    let Some(lighting) = colors.lighting.as_ref().filter(|light| light.enabled) else {
        return;
    };
    let Some(first) = pixels.iter().position(Option::is_some) else {
        return;
    };
    let brightness: [Option<u16>; 8] = std::array::from_fn(|i| {
        surface[i].map(|s| lighting.brightness(s.normal, s.occlusion, s.edge))
    });
    let shaded: [Option<Color>; 8] = std::array::from_fn(|i| {
        pixels[i].map(|index| {
            let base = colors.ink(usize::from(index));
            if flat_ink[i] {
                base
            } else {
                let lit =
                    brightness[i].map_or(base, |value| shade_terminal_color(lighting, base, value));
                if surface[i].is_some_and(|s| s.screen_edge) {
                    silhouette_color(lighting, colors, lit, base, scene)
                } else {
                    lit
                }
            }
        })
    });
    let mono = colors.palette.iter().all(|c| *c == Color::Reset);
    // Monochrome has only glyphs: the visible silhouette takes the densest
    // ramp glyph so the Mon's boundary never fades into faint shading.
    let mono_edge = mono
        && (0..8).any(|i| {
            pixels[i].is_some() && !flat_ink[i] && surface[i].is_some_and(|s| s.screen_edge)
        });
    let ramp_glyph = |value: u16| {
        if mono_edge {
            lighting.glyph(180)
        } else {
            lighting.glyph(value)
        }
    };
    match renderer {
        Renderer::Ascii | Renderer::Auto => {
            if let Some(value) = brightness[first].filter(|_| !flat_ink[first]) {
                cell.set_char(ramp_glyph(value));
            }
            cell.set_fg(shaded[first].unwrap());
        }
        Renderer::HalfBlock if mono => {
            if let Some(value) = brightness[first].filter(|_| !flat_ink[first]) {
                cell.set_char(ramp_glyph(value));
            }
        }
        Renderer::HalfBlock => {
            match (shaded[0], shaded[1]) {
                (Some(upper), Some(lower)) => {
                    cell.set_char(if upper == lower { '█' } else { '▀' })
                        .set_fg(upper)
                        .set_bg(lower);
                }
                (Some(upper), None) => {
                    cell.set_char('▀').set_fg(upper);
                }
                (None, Some(lower)) => {
                    cell.set_char('▄').set_fg(lower);
                }
                _ => {}
            }
            texture_voxel_cell(
                (renderer, lighting),
                pixels,
                surface,
                flat_ink,
                brightness,
                shaded,
                cell,
            );
        }
        Renderer::Quadrant | Renderer::Braille => {
            cell.set_fg(shaded[first].unwrap());
            texture_voxel_cell(
                (renderer, lighting),
                pixels,
                surface,
                flat_ink,
                brightness,
                shaded,
                cell,
            );
        }
    }
}

fn draw_projection(
    sprite: &Sprite,
    renderer: Renderer,
    colors: &Colors,
    projection: Projection,
    area: Rect,
    buf: &mut Buffer,
) {
    let area = area.intersection(buf.area);
    let (dx, dy) = renderer.packing();
    for row in area.y..area.bottom() {
        for col in area.x..area.right() {
            let mut pixels = [None; 8];
            let mut surface = [None; 8];
            for y in 0..dy {
                for x in 0..dx {
                    let i = usize::from(y * dx + x);
                    let px = u32::from(col) * u32::from(dx) + u32::from(x);
                    let py = u32::from(row) * u32::from(dy) + u32::from(y);
                    pixels[i] = projection.sample(sprite, px, py);
                    surface[i] = projected_surface_light(sprite, projection, px, py);
                }
            }
            if let Some(cell) = buf.cell_mut((col, row)) {
                paint_lit_pixels(renderer, colors, pixels, surface, [false; 8], cell);
            }
        }
    }
}

fn head_span(sprite: &Sprite, center: u16, row: u16) -> (f64, f64) {
    let mut left = center;
    let mut right = center;
    while left > 0
        && sprite
            .pixel(u32::from(left - 1), u32::from(row), 1)
            .is_some()
    {
        left -= 1;
    }
    while right + 1 < sprite.width()
        && sprite
            .pixel(u32::from(right + 1), u32::from(row), 1)
            .is_some()
    {
        right += 1;
    }
    (f64::from(left), f64::from(right) + 1.0)
}

fn printable_face(text: &str) -> bool {
    !text.is_empty() && text.is_ascii() && !text.bytes().any(|c| c.is_ascii_control())
}

/// Native glyphs ride the head map near this eye line (head-map v), within
/// this reach either side.
const NATIVE_EYE_LINE: f64 = 0.35;
const NATIVE_BAND_REACH: f64 = 0.16;

/// Where a face tier draws its strokes, so its ink is judged against the head
/// actually under them.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum InkRegion {
    /// Native characters: the head map's eye-line band.
    NativeBand,
    /// Shaded and atlas strokes: the letterboxed text box on the face plane.
    TextBox,
}

impl InkRegion {
    fn contains(self, sprite: &Sprite, x: u32, y: u32) -> bool {
        match self {
            InkRegion::NativeBand => valid_head_uv(sprite, x, y)
                .is_some_and(|uv| (uv[1] - NATIVE_EYE_LINE).abs() <= NATIVE_BAND_REACH),
            InkRegion::TextBox => {
                let Some(texture) = FaceTexture::new(sprite) else {
                    return false;
                };
                let fraction = texture.text_fraction();
                valid_face_uv(sprite, x, y).is_some_and(|uv| {
                    (uv[0] - 0.5).abs() <= fraction[0] / 2.0
                        && (uv[1] - 0.5).abs() <= fraction[1] / 2.0
                })
            }
        }
    }
}

/// The head under a face tier's strokes as the viewer sees it: each texel's
/// material lit by the same model the body uses when lighting is enabled,
/// flat palette material when it is not. A region the sprite does not map
/// falls back to the whole mapped head, then to a legacy face's anchor.
fn face_head_colors(
    sprite: &Sprite,
    colors: &Colors,
    lighting: &crate::lighting::VoxelLighting,
    region: InkRegion,
) -> Vec<(Color, Color)> {
    let mut head = head_colors(sprite, colors, lighting, |x, y| {
        region.contains(sprite, x, y)
    });
    if head.is_empty() {
        head = head_colors(sprite, colors, lighting, |x, y| {
            valid_head_uv(sprite, x, y).is_some() || valid_face_uv(sprite, x, y).is_some()
        });
    }
    if head.is_empty() {
        let anchor = sprite
            .face_overlay()
            .and_then(|face| sprite.pixel(u32::from(face.center_x), u32::from(face.y), 1));
        head.extend(anchor.map(|index| {
            let base = colors.ink(usize::from(index));
            (base, base)
        }));
    }
    head
}

fn head_colors(
    sprite: &Sprite,
    colors: &Colors,
    lighting: &crate::lighting::VoxelLighting,
    include: impl Fn(u32, u32) -> bool,
) -> Vec<(Color, Color)> {
    let mut head = Vec::new();
    for y in 0..u32::from(sprite.height()) {
        for x in 0..u32::from(sprite.width()) {
            if !include(x, y) {
                continue;
            }
            let Some(index) = sprite.pixel(x, y, 1) else {
                continue;
            };
            let base = colors.ink(usize::from(index));
            let seen = match sprite.surface_shading(x, y).filter(|_| lighting.enabled) {
                Some((normal, occlusion)) => {
                    let edge = x
                        .checked_sub(1)
                        .and_then(|x| sprite.pixel(x, y, 1))
                        .is_none()
                        || sprite.pixel(x + 1, y, 1).is_none()
                        || y.checked_sub(1)
                            .and_then(|y| sprite.pixel(x, y, 1))
                            .is_none()
                        || sprite.pixel(x, y + 1, 1).is_none();
                    let brightness = lighting.brightness(normal, occlusion, edge);
                    shade_terminal_color(lighting, base, brightness)
                }
                None => base,
            };
            head.push((seen, base));
        }
    }
    head
}

/// Face strokes use the dark face ink while it reads on most of the head as
/// rendered (lit, or flat when lighting is disabled), measured by the
/// configured outline separation. On a head too dark for it -- a black-bodied
/// species, or shading that darkens a mid-tone body toward the ink -- the
/// candidate legible on the most face texels wins, the earlier on a tie: the
/// ink, the head material's own brightest/darkest lit rims (lighting enabled
/// only), then the palette's materials. Monochrome has no luma and keeps the
/// ink; only test-built Colors carry no lighting parameters at all.
fn face_ink(sprite: &Sprite, colors: &Colors, region: InkRegion) -> Color {
    let ink = colors.ink(0);
    let Some(lighting) = colors.lighting.as_ref() else {
        return ink;
    };
    let head = face_head_colors(sprite, colors, lighting, region);
    let minimum = outline_separation(lighting);
    let legible_on = |candidate: Color| {
        let Some(luma) = luminance(candidate) else {
            return 0;
        };
        head.iter()
            .filter(|(seen, _)| {
                *seen != candidate
                    && luminance(*seen).is_some_and(|head| head.abs_diff(luma) >= minimum)
            })
            .count()
    };
    if legible_on(ink) * 2 >= head.len() {
        return ink;
    }
    let mut materials: Vec<(Color, usize)> = Vec::new();
    for (_, base) in &head {
        match materials.iter_mut().find(|(color, _)| color == base) {
            Some((_, count)) => *count += 1,
            None => materials.push((*base, 1)),
        }
    }
    let dominant = materials
        .iter()
        .fold(
            None,
            |best: Option<(Color, usize)>, &(color, count)| match best {
                Some((_, most)) if most >= count => best,
                _ => Some((color, count)),
            },
        )
        .map(|(color, _)| color);
    let rims = dominant
        .filter(|_| lighting.enabled)
        .map(|base| {
            [
                shade_terminal_color(lighting, base, 180),
                shade_terminal_color(lighting, base, 0),
            ]
        })
        .into_iter()
        .flatten();
    std::iter::once(ink)
        .chain(rims)
        .chain(colors.palette.iter().copied())
        .fold((ink, 0), |(best, most), candidate| {
            let count = legible_on(candidate);
            if count > most {
                (candidate, count)
            } else {
                (best, most)
            }
        })
        .0
}

fn paint_face_character(
    cell: &mut ratatui::buffer::Cell,
    colors: &Colors,
    ink: Color,
    glyph: char,
) {
    cell.set_char(glyph).set_fg(ink);
    if colors.palette.iter().all(|color| *color == Color::Reset) {
        // Native characters must remain distinct even when the body tone uses
        // that same ASCII character and the terminal has no color channels.
        cell.set_style(
            ratatui::style::Style::default().add_modifier(ratatui::style::Modifier::BOLD),
        );
    }
}

/// A depth-tested front anchor authorizes the face. Packed-pixel holes do not
/// veto all five letters; inadequate head resolution uses an attached caption.
fn draw_front_face(
    sprite: &Sprite,
    renderer: Renderer,
    colors: &Colors,
    projection: Projection,
    area: Rect,
    buf: &mut Buffer,
) -> bool {
    let area = area.intersection(buf.area);
    let Some(face) = sprite.face_overlay() else {
        return false;
    };
    if !printable_face(face.text) {
        return false;
    }
    let (center, row) = projection.cell(
        f64::from(face.center_x) + 0.5,
        f64::from(face.y) + 0.5,
        renderer,
    );
    let length = face.text.len() as i32;
    let start = center.floor() as i32 - length / 2;
    let row = row.floor() as i32;
    if start < i32::from(area.x)
        || start + length > i32::from(area.right())
        || row < i32::from(area.y)
        || row >= i32::from(area.bottom())
    {
        return false;
    }
    let (left, right) = head_span(sprite, face.center_x, face.y);
    let (head_left, _) = projection.cell(left, f64::from(face.y), renderer);
    let (head_right, _) = projection.cell(right, f64::from(face.y), renderer);
    if head_right - head_left < f64::from(length)
        || f64::from(start) < head_left.floor()
        || f64::from(start + length) > head_right.ceil()
    {
        return false;
    }
    let ink = face_ink(sprite, colors, InkRegion::NativeBand);
    for (offset, glyph) in face.text.chars().enumerate() {
        if let Some(cell) = buf.cell_mut(((start + offset as i32) as u16, row as u16)) {
            paint_face_character(cell, colors, ink, glyph);
        }
    }
    true
}

/// Thin 5x9 glyphs with a three-column side bearing. Parentheses are curves,
/// not chevrons; eyes occupy the upper rows and the mouth has its own baseline.
/// Geometry and texture resolution are independent of the terminal packing.
fn face_glyph(glyph: u8) -> Option<[u8; 9]> {
    Some(match glyph {
        b'(' => [2, 4, 8, 16, 16, 16, 8, 4, 2],
        b')' => [8, 4, 2, 1, 1, 1, 2, 4, 8],
        b'^' => [0, 4, 10, 17, 0, 0, 0, 0, 0],
        b'_' => [0, 0, 0, 0, 0, 0, 0, 0, 31],
        b'o' => [0, 0, 14, 17, 17, 17, 14, 0, 0],
        b'O' => [14, 17, 17, 17, 17, 17, 14, 0, 0],
        b'-' => [0, 0, 0, 0, 31, 0, 0, 0, 0],
        b'T' => [31, 4, 4, 4, 4, 4, 4, 0, 0],
        b';' => [0, 4, 0, 0, 0, 4, 4, 8, 0],
        b'>' => [16, 8, 4, 2, 1, 2, 4, 8, 16],
        b'<' => [1, 2, 4, 8, 16, 8, 4, 2, 1],
        b'\'' => [2, 2, 4, 0, 0, 0, 0, 0, 0],
        96 => [8, 8, 4, 0, 0, 0, 0, 0, 0],
        b'=' => [0, 0, 31, 0, 0, 31, 0, 0, 0],
        b'+' => [0, 0, 4, 4, 31, 4, 4, 0, 0],
        b'@' => [14, 17, 23, 21, 23, 16, 14, 0, 0],
        b'u' => [0, 0, 17, 17, 17, 17, 14, 0, 0],
        b'~' => [0, 0, 0, 8, 21, 2, 0, 0, 0],
        b'.' => [0, 0, 0, 0, 0, 0, 0, 4, 0],
        b',' => [0, 0, 0, 0, 0, 0, 4, 4, 8],
        b'*' => [0, 0, 4, 21, 14, 21, 4, 0, 0],
        b'v' => [0, 0, 17, 17, 10, 10, 4, 0, 0],
        b'x' => [0, 0, 17, 10, 4, 10, 17, 0, 0],
        b'w' => [0, 0, 17, 17, 21, 21, 10, 0, 0],
        b'3' => [14, 17, 1, 2, 6, 1, 17, 14, 0],
        b'Q' => [14, 17, 17, 17, 21, 18, 13, 1, 0],
        _ => return None,
    })
}

#[derive(Clone, Copy, Debug)]
struct FaceTexture {
    text: &'static str,
    /// Physical width/height of the face plane. None keeps the legacy mapping
    /// that stretches the text box over the whole UV square.
    aspect: Option<f64>,
}

impl FaceTexture {
    fn new(sprite: &Sprite) -> Option<Self> {
        let text = sprite.face_text()?;
        (printable_face(text) && text.bytes().all(|b| face_glyph(b).is_some())).then_some(Self {
            text,
            aspect: sprite.face_aspect(),
        })
    }

    fn width(self) -> f64 {
        (self.text.len() * 8 - 3) as f64
    }

    /// The glyph grid including its one-unit margins.
    fn grid_box(self) -> [f64; 2] {
        [self.width() + 2.0, 11.0]
    }

    /// Grid units per unit of UV. With a known face plane the text box is
    /// fitted uniformly (letterboxed and centred), so `o` stays round and a
    /// tall head cannot stretch the eyes into bars.
    fn grid_per_uv(self) -> [f64; 2] {
        let grid = self.grid_box();
        match self.aspect.filter(|a| a.is_finite() && *a > 0.0) {
            None => grid,
            Some(aspect) => {
                let per_height = (grid[0] / aspect).max(grid[1]);
                [per_height * aspect, per_height]
            }
        }
    }

    /// Share of the face plane the text box occupies on each axis (1 = all).
    fn text_fraction(self) -> [f64; 2] {
        let grid = self.grid_box();
        let scale = self.grid_per_uv();
        [grid[0] / scale[0], grid[1] / scale[1]]
    }

    fn bit(self, x: i32, y: i32) -> bool {
        if x < 0 || !(0..9).contains(&y) {
            return false;
        }
        let glyph = x as usize / 8;
        let column = x as usize % 8;
        if column >= 5 {
            return false;
        }
        self.text
            .as_bytes()
            .get(glyph)
            .and_then(|b| face_glyph(*b))
            .is_some_and(|rows| rows[y as usize] & (1 << (4 - column)) != 0)
    }

    fn sample(self, uv: [f64; 2], footprint: [f64; 2]) -> bool {
        self.coverage(uv, footprint, 0.75) >= 0.4
    }

    /// Box-filtered share of glyph ink under a footprint, 0..=1. The atlas caps
    /// its filter at 0.75 grid units; the shaded tier filters a whole cell.
    fn coverage(self, uv: [f64; 2], footprint: [f64; 2], max_radius: f64) -> f64 {
        let size = self.grid_box();
        let scale = self.grid_per_uv();
        let grid = if self.aspect.is_some() {
            [
                scale[0] * (uv[0] - 0.5) + size[0] / 2.0 - 1.0,
                scale[1] * (uv[1] - 0.5) + size[1] / 2.0 - 1.0,
            ]
        } else {
            [size[0] * uv[0] - 1.0, size[1] * uv[1] - 1.0]
        };
        // Coverage, rather than ANY touched ink, keeps side bearings open.
        // Capping the filter prevents minification from growing a solid face.
        // Underresolved heads use independently surface-anchored terminal glyphs.
        let radius = [
            (scale[0] * footprint[0].abs() / 2.0).clamp(0.025, max_radius),
            (scale[1] * footprint[1].abs() / 2.0).clamp(0.025, max_radius),
        ];
        let left = (grid[0] - radius[0]).floor() as i32;
        let right = (grid[0] + radius[0]).floor() as i32;
        let top = (grid[1] - radius[1]).floor() as i32;
        let bottom = (grid[1] + radius[1]).floor() as i32;
        let mut coverage = 0.0;
        for y in top..=bottom {
            for x in left..=right {
                if self.bit(x, y) {
                    let overlap = |center: f64, radius: f64, start: i32| {
                        ((center + radius).min(f64::from(start) + 1.0)
                            - (center - radius).max(f64::from(start)))
                        .max(0.0)
                    };
                    coverage += overlap(grid[0], radius[0], x) * overlap(grid[1], radius[1], y);
                }
            }
        }
        coverage / (4.0 * radius[0] * radius[1])
    }

    fn bounds(sprite: &Sprite) -> Option<SolidBounds> {
        let mut bounds = SolidBounds {
            left: f64::from(sprite.width()),
            top: f64::from(sprite.height()),
            right: 0.0,
            bottom: 0.0,
        };
        for y in 0..u32::from(sprite.height()) {
            for x in 0..u32::from(sprite.width()) {
                if valid_face_uv(sprite, x, y).is_some() {
                    bounds.left = bounds.left.min(f64::from(x));
                    bounds.top = bounds.top.min(f64::from(y));
                    bounds.right = bounds.right.max(f64::from(x) + 1.0);
                    bounds.bottom = bounds.bottom.max(f64::from(y) + 1.0);
                }
            }
        }
        (bounds.right > bounds.left && bounds.bottom > bounds.top).then_some(bounds)
    }
}

fn valid_face_uv(sprite: &Sprite, x: u32, y: u32) -> Option<[f64; 2]> {
    sprite.pixel(x, y, 1)?;
    let uv = sprite.face_uv(x, y)?;
    if uv.iter().all(|v| v.is_finite() && (0.0..=1.0).contains(v)) {
        Some(uv.map(f64::from))
    } else {
        None
    }
}

/// Full visible head coverage is for native characters; the narrow face UV
/// remains the atlas contract. Both maps have identical winning-depth authority.
fn valid_head_uv(sprite: &Sprite, x: u32, y: u32) -> Option<[f64; 2]> {
    sprite.pixel(x, y, 1)?;
    let uv = sprite.head_uv(x, y)?;
    uv.iter()
        .all(|v| v.is_finite() && (0.0..=1.0).contains(v))
        .then(|| uv.map(f64::from))
}

fn head_surface_bounds(sprite: &Sprite) -> Option<SolidBounds> {
    let mut bounds = SolidBounds {
        left: f64::from(sprite.width()),
        top: f64::from(sprite.height()),
        right: 0.0,
        bottom: 0.0,
    };
    for y in 0..u32::from(sprite.height()) {
        for x in 0..u32::from(sprite.width()) {
            if valid_head_uv(sprite, x, y).is_some() {
                bounds.left = bounds.left.min(f64::from(x));
                bounds.top = bounds.top.min(f64::from(y));
                bounds.right = bounds.right.max(f64::from(x) + 1.0);
                bounds.bottom = bounds.bottom.max(f64::from(y) + 1.0);
            }
        }
    }
    (bounds.right > bounds.left && bounds.bottom > bounds.top).then_some(bounds)
}

fn projected_head_uv(
    sprite: &Sprite,
    projection: Projection,
    x: u32,
    y: u32,
) -> Option<([f64; 2], [f64; 2])> {
    projected_surface_uv(sprite, projection, x, y, valid_head_uv, 0.25, false)
}

/// Face-map continuity is authority, not a step size. The face map is an
/// orthographic projection of one plane: occluders, noses and rear views are
/// holes (no authorized UV), never jumps, and every authorized UV lies in the
/// unit square, so a limit of 1.0 accepts every authorized neighbour. Coarse
/// faces need it -- an eight-bit face is three or four texels tall (steps up
/// to 0.45) and a two-texel face steps past 0.5; a tighter cutoff dropped the
/// derivative and every cell row inside a texel sampled one glyph band. Being
/// a plane it also has one gradient: an edge texel with no face neighbour
/// above or below (where the parentheses sit) borrows the adjacent texel's,
/// which stays zero on a genuinely grazing facet.
const FACE_UV_MAX_STEP: f64 = 1.0;

/// The same nearest material sample authorizes every interpolated UV.
/// Derivatives refine ink within that pixel, never its depth/visibility mask.
fn projected_face_uv(
    sprite: &Sprite,
    projection: Projection,
    x: u32,
    y: u32,
) -> Option<([f64; 2], [f64; 2])> {
    projected_surface_uv(
        sprite,
        projection,
        x,
        y,
        valid_face_uv,
        FACE_UV_MAX_STEP,
        true,
    )
}

/// A texel's UV and its derivative per source pixel along x and y; `None`
/// when no continuous neighbour exists on that axis.
type TexelUv = ([f64; 2], Option<[f64; 2]>, Option<[f64; 2]>);

fn texel_derivatives(
    sprite: &Sprite,
    lookup: fn(&Sprite, u32, u32) -> Option<[f64; 2]>,
    max_step: f64,
    (sx, sy): (u32, u32),
) -> Option<TexelUv> {
    let uv = lookup(sprite, sx, sy)?;
    let neighbor = |x, y| {
        lookup(sprite, x, y).filter(|next| {
            (next[0] - uv[0]).abs() <= max_step && (next[1] - uv[1]).abs() <= max_step
        })
    };
    let derivative = |forward: Option<[f64; 2]>, backward: Option<[f64; 2]>| -> Option<[f64; 2]> {
        match (forward, backward) {
            (Some(a), Some(b)) => Some(std::array::from_fn(|i| (a[i] - b[i]) / 2.0)),
            (Some(a), None) => Some(std::array::from_fn(|i| a[i] - uv[i])),
            (None, Some(b)) => Some(std::array::from_fn(|i| uv[i] - b[i])),
            (None, None) => None,
        }
    };
    Some((
        uv,
        derivative(
            neighbor(sx + 1, sy),
            sx.checked_sub(1).and_then(|x| neighbor(x, sy)),
        ),
        derivative(
            neighbor(sx, sy + 1),
            sy.checked_sub(1).and_then(|y| neighbor(sx, y)),
        ),
    ))
}

fn projected_surface_uv(
    sprite: &Sprite,
    projection: Projection,
    x: u32,
    y: u32,
    lookup: fn(&Sprite, u32, u32) -> Option<[f64; 2]>,
    max_step: f64,
    planar: bool,
) -> Option<([f64; 2], [f64; 2])> {
    let (sx, sy) = projection.source_pixel(sprite, x, y)?;
    let (uv, horizontal, vertical) = texel_derivatives(sprite, lookup, max_step, (sx, sy))?;
    // A planar map has one gradient, so an axis without a neighbour borrows
    // it from the adjacent texel on the same continuous surface.
    let borrow = |axis: fn(TexelUv) -> Option<[f64; 2]>, texels: [Option<(u32, u32)>; 2]| {
        texels.into_iter().flatten().find_map(|texel| {
            let next = texel_derivatives(sprite, lookup, max_step, texel)?;
            let continuous = (0..2).all(|i| (next.0[i] - uv[i]).abs() <= max_step);
            continuous.then(|| axis(next)).flatten()
        })
    };
    let horizontal = horizontal
        .or_else(|| {
            planar
                .then(|| {
                    borrow(
                        |texel| texel.1,
                        [Some((sx, sy + 1)), sy.checked_sub(1).map(|y| (sx, y))],
                    )
                })
                .flatten()
        })
        .unwrap_or([0.0; 2]);
    let vertical = vertical
        .or_else(|| {
            planar
                .then(|| {
                    borrow(
                        |texel| texel.2,
                        [Some((sx + 1, sy)), sx.checked_sub(1).map(|x| (x, sy))],
                    )
                })
                .flatten()
        })
        .unwrap_or([0.0; 2]);
    let offset = [
        (f64::from(x) + 0.5 - projection.left) / projection.scale - f64::from(sx) - 0.5,
        (f64::from(y) + 0.5 - projection.top) / projection.scale_y - f64::from(sy) - 0.5,
    ];
    let refined = std::array::from_fn(|i| {
        (uv[i] + horizontal[i] * offset[0] + vertical[i] * offset[1]).clamp(0.0, 1.0)
    });
    let footprint = std::array::from_fn(|i| {
        horizontal[i].abs() / projection.scale + vertical[i].abs() / projection.scale_y
    });
    Some((refined, footprint))
}

type NativeHeadCell = ((u16, u16), [f64; 2], f64);
type NativeGlyphCell = Option<((u16, u16), u8)>;

/// Allocate mandatory eyes and mouth before optional punctuation. A parenthesis
/// must never consume the only legal cell for a complete expression core.
fn native_glyph_candidates(
    texture: FaceTexture,
    band: f64,
    visible: &[NativeHeadCell],
) -> Vec<NativeGlyphCell> {
    let mut candidates = vec![None; texture.text.len()];
    if candidates.len() < 3 {
        return candidates;
    }
    let last = candidates.len() - 1;
    let mut used = Vec::new();
    let horizontal_tolerance = 3.0 / (texture.width() + 2.0);
    for index in (1..last).chain([0, last]) {
        let glyph = texture.text.as_bytes()[index];
        let target_u = (index as f64 * 8.0 + 3.5) / (texture.width() + 2.0);
        let mut best = None;
        let mut best_distance = f64::INFINITY;
        for &(pos, uv, width) in visible {
            if used.contains(&pos) {
                continue;
            }
            let du = (uv[0] - target_u) / horizontal_tolerance.max(width * 0.55);
            let dv = (uv[1] - band) / NATIVE_BAND_REACH;
            let distance = du * du + dv * dv;
            if du.abs() <= 1.0 && dv.abs() <= 1.0 && distance < best_distance {
                best = Some(pos);
                best_distance = distance;
            }
        }
        if let Some(pos) = best {
            used.push(pos);
        }
        candidates[index] = best.map(|pos| (pos, glyph));
    }
    candidates
}

/// A two-row head needs a connected text run, otherwise body shading can
/// look like an extra eye or mouth between its native facial characters.
/// Every candidate is still a complete depth-authorized head cell, and both
/// eye anchors must straddle the head's actual UV centre.
fn native_micro_candidates(
    texture: FaceTexture,
    visible: &[NativeHeadCell],
) -> Vec<NativeGlyphCell> {
    let length = texture.text.len();
    let empty = vec![None; length];
    if length < 3 {
        return empty;
    }
    let middle = length / 2;
    let pitch = 8.0 / (texture.width() + 2.0);
    for indices in [0..length, 1..length - 1] {
        let mut best = None;
        let mut best_score = f64::INFINITY;
        for &(start, _, _) in visible {
            let mut candidates = empty.clone();
            let mut score = 0.0;
            let mut previous: Option<[f64; 2]> = None;
            let mut complete = true;
            for (offset, index) in indices.clone().enumerate() {
                let Some(column) = start.0.checked_add(offset as u16) else {
                    complete = false;
                    break;
                };
                let Some(&(_, uv, _)) =
                    visible.iter().find(|(pos, _, _)| *pos == (column, start.1))
                else {
                    complete = false;
                    break;
                };
                let target_u = (index as f64 * 8.0 + 3.5) / (texture.width() + 2.0);
                if (uv[0] - target_u).abs() > pitch
                    || (index < middle && uv[0] >= 0.5)
                    || (index > middle && uv[0] <= 0.5)
                    || previous.is_some_and(|p| uv[0] <= p[0] || (uv[1] - p[1]).abs() > 0.16)
                {
                    complete = false;
                    break;
                }
                score += (uv[0] - target_u).powi(2) + (uv[1] - NATIVE_EYE_LINE).powi(2);
                previous = Some(uv);
                candidates[index] = Some(((column, start.1), texture.text.as_bytes()[index]));
            }
            if complete && score < best_score {
                best = Some(candidates);
                best_score = score;
            }
        }
        if let Some(candidates) = best {
            return candidates;
        }
    }
    empty
}

/// Face level of detail, measured on the letterboxed text box per glyph.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum FaceLod {
    /// Terminal characters: one cell per glyph.
    Native,
    /// The glyph bitmap as multi-cell ASCII shading.
    Shaded,
    /// The glyph bitmap in packed pixels.
    Atlas,
}

/// Cells per glyph and rows the letterboxed text box needs for each tier.
/// Below the shaded minimum a glyph cannot be drawn legibly across several
/// cells, so native characters stay. Packed-pixel renderers keep the original
/// 5-cell/4.5-row atlas switch; one-pixel-per-cell ASCII would only stamp
/// binary marks there and keeps the shading at every larger size.
const SHADED_MIN_CELLS_PER_GLYPH: f64 = 4.0;
const SHADED_MIN_ROWS: f64 = 4.0;
const ATLAS_MIN_CELLS_PER_GLYPH: f64 = 5.0;
const ATLAS_MIN_ROWS: f64 = 4.5;
/// A cell shows face ink only when its glyph coverage reaches this share.
const SHADED_MIN_INK: f64 = 0.15;

fn face_lod(texture: FaceTexture, columns: f64, rows: f64, pixel_atlas: bool) -> FaceLod {
    let fraction = texture.text_fraction();
    let per_glyph = columns * fraction[0] / texture.text.len() as f64;
    let rows = rows * fraction[1];
    if per_glyph < SHADED_MIN_CELLS_PER_GLYPH || rows < SHADED_MIN_ROWS {
        FaceLod::Native
    } else if pixel_atlas && per_glyph >= ATLAS_MIN_CELLS_PER_GLYPH && rows >= ATLAS_MIN_ROWS {
        FaceLod::Atlas
    } else {
        FaceLod::Shaded
    }
}

/// Intermediate faces: the kaomoji drawn as multi-cell ASCII art on the face
/// plane, like the voxel shading overlay. Each cell's ramp glyph density
/// follows how much of the bitmap's ink it covers, normalized so the face's
/// densest stroke takes the densest glyph at every scale. Only cells wholly on
/// the mapped face plane take part, so noses, occluders and rear views keep
/// their surface. One-plane ASCII restores the lit body exactly first, as the
/// atlas does, so stale marks never survive in stroke gaps; packed cells keep
/// their pixels as the background behind the stroke's glyph and face ink.
fn draw_shaded_face(
    sprite: &Sprite,
    renderer: Renderer,
    colors: &Colors,
    projection: Projection,
    area: Rect,
    buf: &mut Buffer,
    face: (FaceTexture, [f64; 4], Color),
) -> bool {
    let (texture, [left, top, right, bottom], stroke_color) = face;
    let (dx, dy) = renderer.packing();
    let count = f64::from(dx * dy);
    let mut cells = Vec::new();
    for row in (top.floor() as i32).max(i32::from(area.y))
        ..(bottom.ceil() as i32).min(i32::from(area.bottom()))
    {
        for col in (left.floor() as i32).max(i32::from(area.x))
            ..(right.ceil() as i32).min(i32::from(area.right()))
        {
            let mut original = [None; 8];
            let mut surface = [None; 8];
            let mut ink = 0.0;
            let mut covered = 0;
            for y in 0..dy {
                for x in 0..dx {
                    let i = usize::from(y * dx + x);
                    let px = col as u32 * u32::from(dx) + u32::from(x);
                    let py = row as u32 * u32::from(dy) + u32::from(y);
                    original[i] = projection.sample(sprite, px, py);
                    surface[i] = projected_surface_light(sprite, projection, px, py);
                    // Each packed pixel filters its own footprint; the cell is their mean.
                    if let Some((uv, footprint)) = projected_face_uv(sprite, projection, px, py) {
                        covered += 1;
                        ink += texture.coverage(uv, footprint, f64::INFINITY) / count;
                    }
                }
            }
            if covered == dx * dy {
                let ink = (ink >= SHADED_MIN_INK).then_some(ink.min(1.0));
                cells.push((col as u16, row as u16, ink, original, surface));
            }
        }
    }
    let peak = cells.iter().filter_map(|cell| cell.2).fold(0.0, f64::max);
    // A packed cell's background already holds body pixels, so relighting it
    // would not reproduce the body; ASCII's background is the room's.
    let restore = (dx, dy) == (1, 1);
    let mut painted = false;
    for (col, row, ink, original, surface) in cells {
        let Some(cell) = buf.cell_mut((col, row)) else {
            continue;
        };
        if restore {
            let background = cell.bg;
            paint_lit_pixels(renderer, colors, original, surface, [false; 8], cell);
            cell.set_bg(background);
            painted = true;
        }
        let Some(ink) = ink else {
            continue;
        };
        let body = cell.symbol().to_string();
        let level = (ink / peak * 180.0).round() as u16;
        let glyph = colors.lighting.as_ref().map_or('#', |light| {
            let glyph = light.glyph(level);
            // Monochrome has no ink colour: a stroke never repeats the body glyph.
            if glyph.to_string() == body {
                light.glyph(180)
            } else {
                glyph
            }
        });
        let glyph = if glyph.to_string() == body {
            '#'
        } else {
            glyph
        };
        cell.set_char(glyph).set_fg(stroke_color);
        painted = true;
    }
    painted
}

/// A head with room for the whole expression on one row draws it as one
/// connected word: anchors spread across a broad head read as separate marks
/// and let a muzzle sit in the middle of the expression. Every cell is still a
/// complete, depth-authorized head cell (a nose or occluder wins its cells),
/// u increases along the run, rows stay on one surface band, and the eye
/// halves straddle the head's UV centre. The run nearest the eye line and the
/// head's centre wins; None keeps the anchored placement.
fn native_run_candidates(
    texture: FaceTexture,
    visible: &[NativeHeadCell],
) -> Option<Vec<NativeGlyphCell>> {
    let length = texture.text.len();
    if length < 3 {
        return None;
    }
    let middle = length / 2;
    let mut best: Option<(f64, Vec<NativeGlyphCell>)> = None;
    for &(start, _, _) in visible {
        let mut run: Vec<((u16, u16), [f64; 2])> = Vec::with_capacity(length);
        for offset in 0..length {
            let Some(column) = start.0.checked_add(offset as u16) else {
                break;
            };
            let Some(&(_, uv, _)) = visible.iter().find(|(pos, _, _)| *pos == (column, start.1))
            else {
                break;
            };
            if run
                .last()
                .is_some_and(|(_, p)| uv[0] <= p[0] || (uv[1] - p[1]).abs() > 0.16)
            {
                break;
            }
            run.push(((column, start.1), uv));
        }
        if run.len() != length
            || run[..middle].iter().any(|(_, uv)| uv[0] >= 0.5)
            || run[middle + 1..].iter().any(|(_, uv)| uv[0] <= 0.5)
        {
            continue;
        }
        let centre = run[middle].1;
        let score = (centre[0] - 0.5).powi(2) + (centre[1] - NATIVE_EYE_LINE).powi(2);
        if best.as_ref().is_none_or(|(previous, _)| score < *previous) {
            let cells = run
                .iter()
                .zip(texture.text.bytes())
                .map(|((pos, _), glyph)| Some((*pos, glyph)))
                .collect();
            best = Some((score, cells));
        }
    }
    best.map(|(_, cells)| cells)
}

/// Terminal-sized glyphs are a level of detail for an underresolved atlas.
/// Each character finds its own visible head-local UV anchor. Its entire packed
/// cell must be mapped head material, so a nose, ear or occluder wins outright.
/// These anchors turn and roll with the head; no screen-axis string is stamped.
fn draw_surface_glyphs(
    sprite: &Sprite,
    renderer: Renderer,
    colors: &Colors,
    projection: Projection,
    area: Rect,
    buf: &mut Buffer,
) -> bool {
    let Some(texture) = FaceTexture::new(sprite) else {
        return false;
    };
    let ink = face_ink(sprite, colors, InkRegion::NativeBand);
    let (dx, dy) = renderer.packing();
    let mut visible = Vec::new();
    for row in area.y..area.bottom() {
        for col in area.x..area.right() {
            let mut mean = [0.0; 2];
            let mut cell_width = 0.0;
            let mut complete = true;
            for y in 0..dy {
                for x in 0..dx {
                    if let Some((uv, footprint)) = projected_head_uv(
                        sprite,
                        projection,
                        u32::from(col) * u32::from(dx) + u32::from(x),
                        u32::from(row) * u32::from(dy) + u32::from(y),
                    ) {
                        cell_width += footprint[0] * f64::from(dx) / f64::from(dx * dy);
                        for (dest, value) in mean.iter_mut().zip(uv) {
                            *dest += value / f64::from(dx * dy);
                        }
                    } else {
                        complete = false;
                    }
                }
            }
            if complete {
                visible.push(((col, row), mean, cell_width));
            }
        }
    }
    if area.height <= 3 {
        let candidates = native_micro_candidates(texture, &visible);
        let mut painted = false;
        for (pos, glyph) in candidates.into_iter().flatten() {
            if let Some(cell) = buf.cell_mut(pos) {
                paint_face_character(cell, colors, ink, char::from(glyph));
                painted = true;
            }
        }
        return painted;
    }
    if let Some(run) = native_run_candidates(texture, &visible) {
        let mut painted = false;
        for (pos, glyph) in run.into_iter().flatten() {
            if let Some(cell) = buf.cell_mut(pos) {
                // Preserve the room plane, including terminal tint/opacity.
                paint_face_character(cell, colors, ink, char::from(glyph));
                painted = true;
            }
        }
        return painted;
    }
    let horizontal_tolerance = 3.0 / (texture.width() + 2.0);
    // Start with the upper face. A broad beak can consume a whole terminal
    // cell there, so derive alternate rows from actual legal mouth cells.
    // Every fallback row still belongs to the nearest visible mapped head.
    let mut bands = vec![NATIVE_EYE_LINE];
    bands.extend(
        visible
            .iter()
            .filter(|(_, uv, width)| (uv[0] - 0.5).abs() <= horizontal_tolerance.max(width * 0.55))
            .map(|(_, uv, _)| uv[1]),
    );
    bands.sort_by(|a, b| {
        (a - NATIVE_EYE_LINE)
            .abs()
            .total_cmp(&(b - NATIVE_EYE_LINE).abs())
    });
    bands.dedup_by(|a, b| (*a - *b).abs() < 0.001);
    let mut compact = None;
    for band in bands {
        let mut candidates = native_glyph_candidates(texture, band, &visible);
        // Collect first: never paint a missing-mouth or single-eye fragment.
        if candidates.len() < 3
            || candidates[1..candidates.len() - 1]
                .iter()
                .any(Option::is_none)
        {
            continue;
        }
        let last = candidates.len() - 1;
        if candidates[0].is_none() || candidates[last].is_none() {
            candidates[0] = None;
            candidates[last] = None;
            if compact.is_none() {
                compact = Some(candidates);
            }
            continue;
        }
        let mut painted = false;
        for (pos, glyph) in candidates.into_iter().flatten() {
            if let Some(cell) = buf.cell_mut(pos) {
                // Preserve the room plane, including terminal tint/opacity.
                paint_face_character(cell, colors, ink, char::from(glyph));
                painted = true;
            }
        }
        return painted;
    }
    if let Some(candidates) = compact {
        for (pos, glyph) in candidates.into_iter().flatten() {
            if let Some(cell) = buf.cell_mut(pos) {
                paint_face_character(cell, colors, ink, char::from(glyph));
            }
        }
        return true;
    }
    false
}

/// Large expressions are textures on the actual head surface, transformed and
/// occluded by the sprite's depth-tested UV map, with glyph LOD on small heads.
fn draw_camera_face(
    sprite: &Sprite,
    renderer: Renderer,
    colors: &Colors,
    projection: Projection,
    area: Rect,
    buf: &mut Buffer,
) -> bool {
    let area = area.intersection(buf.area);
    let Some(texture) = FaceTexture::new(sprite) else {
        // Mapped voxels never bypass depth authority, even in a tiny viewport.
        return sprite.face_text().is_none()
            && draw_front_face(sprite, renderer, colors, projection, area, buf);
    };
    let Some(head_bounds) = head_surface_bounds(sprite) else {
        return false;
    };
    let (head_left, _) = projection.cell(head_bounds.left, head_bounds.top, renderer);
    let (head_right, _) = projection.cell(head_bounds.right, head_bounds.bottom, renderer);
    let native_fits = head_right - head_left >= texture.text.len() as f64;
    if area.height <= 3 {
        return native_fits && draw_surface_glyphs(sprite, renderer, colors, projection, area, buf);
    }
    let Some(bounds) = FaceTexture::bounds(sprite) else {
        return native_fits && draw_surface_glyphs(sprite, renderer, colors, projection, area, buf);
    };
    // Low detail uses independent UV anchors; fine detail samples the atlas.
    // A severely minified/foreshortened surface keeps the separate HUD caption
    // rather than forcing a five-character billboard into an unresolvable head.
    let (dx, dy) = renderer.packing();
    let (left, top) = projection.cell(bounds.left, bounds.top, renderer);
    let (right, bottom) = projection.cell(bounds.right, bounds.bottom, renderer);
    let columns = right - left;
    let rows = bottom - top;
    // Native cells use full visible head coverage, including the forehead.
    // Resolution comes from the letterboxed text box on the narrow face
    // surface: native characters, then multi-cell ASCII shading, then the atlas.
    // One pixel per cell has no sub-cell atlas to switch to, and half-block
    // atlas ink is a flat palette pixel: a head that needs a substitute ink
    // (see face_ink) keeps the shaded strokes, whose glyphs carry it.
    let stroke_color = face_ink(sprite, colors, InkRegion::TextBox);
    let pixel_atlas = renderer.packing() != (1, 1)
        && !(renderer == Renderer::HalfBlock && stroke_color != colors.ink(0));
    match face_lod(texture, columns, rows, pixel_atlas) {
        FaceLod::Native => {
            return native_fits
                && draw_surface_glyphs(sprite, renderer, colors, projection, area, buf);
        }
        FaceLod::Shaded => {
            return draw_shaded_face(
                sprite,
                renderer,
                colors,
                projection,
                area,
                buf,
                (texture, [left, top, right, bottom], stroke_color),
            );
        }
        FaceLod::Atlas => {}
    }
    let mono = colors.palette.iter().all(|c| *c == Color::Reset);
    let mut painted = false;
    for row in (top.floor() as i32).max(i32::from(area.y))
        ..(bottom.ceil() as i32).min(i32::from(area.bottom()))
    {
        for col in (left.floor() as i32).max(i32::from(area.x))
            ..(right.ceil() as i32).min(i32::from(area.right()))
        {
            let mut pixels = [None; 8];
            let mut original = [None; 8];
            let mut surface = [None; 8];
            let mut ink = [false; 8];
            let mut covered = [false; 8];
            for y in 0..dy {
                for x in 0..dx {
                    let i = usize::from(y * dx + x);
                    let px = col as u32 * u32::from(dx) + u32::from(x);
                    let py = row as u32 * u32::from(dy) + u32::from(y);
                    pixels[i] = projection.sample(sprite, px, py);
                    original[i] = pixels[i];
                    surface[i] = projected_surface_light(sprite, projection, px, py);
                    if let Some((uv, footprint)) = projected_face_uv(sprite, projection, px, py) {
                        covered[i] = true;
                        ink[i] = texture.sample(uv, footprint);
                        if ink[i] {
                            pixels[i] = Some(0);
                        }
                    }
                }
            }
            if !covered.iter().any(|v| *v) {
                continue;
            }
            let count = usize::from(dx * dy);
            let full_cell = covered[..count].iter().all(|v| *v);
            let Some(cell) = buf.cell_mut((col as u16, row as u16)) else {
                continue;
            };
            match renderer {
                Renderer::Auto | Renderer::Ascii => {
                    // ASCII has one foreground plane. Restore its actual body
                    // glyph, then draw strokes without changing the room's
                    // background (terminals may tint that plane independently).
                    let background = cell.bg;
                    paint_lit_pixels(renderer, colors, original, surface, [false; 8], cell);
                    cell.set_bg(background);
                    if ink[0] {
                        let mark = if mono {
                            if cell.symbol() == "@" {
                                '#'
                            } else {
                                '@'
                            }
                        } else {
                            '#'
                        };
                        cell.set_char(mark).set_fg(stroke_color);
                    }
                }
                Renderer::HalfBlock if mono => {
                    if !full_cell {
                        continue;
                    }
                    let background = cell.bg;
                    paint_lit_pixels(renderer, colors, original, surface, [false; 8], cell);
                    cell.set_bg(background);
                    if ink[0] || ink[1] {
                        cell.set_char(if cell.symbol() == "@" { '#' } else { '@' });
                    }
                }
                Renderer::HalfBlock => {
                    paint_lit_pixels(renderer, colors, pixels, surface, ink, cell)
                }
                Renderer::Quadrant | Renderer::Braille => {
                    // One-foreground pattern cells carve expression strokes
                    // from the actual head color. Never paint a face rectangle
                    // into the terminal's separately treated background plane.
                    // Mixed cells keep the real nose/ear/occluder untouched.
                    if !full_cell {
                        continue;
                    }
                    let background = cell.bg;
                    paint_lit_pixels(renderer, colors, original, surface, [false; 8], cell);
                    cell.set_bg(background);
                    let backing = cell.fg;
                    let mask = ink.map(|v| !v);
                    let glyph = if renderer == Renderer::Quadrant {
                        quadrant(std::array::from_fn(|i| mask[i]))
                    } else {
                        braille(mask)
                    };
                    cell.set_char(glyph).set_fg(backing);
                }
            }
            painted |= ink.iter().any(|on| *on);
        }
    }
    painted
}

fn draw_caption(text: &str, center: f64, row: i32, area: Rect, colors: &Colors, buf: &mut Buffer) {
    let area = area.intersection(buf.area);
    if !printable_face(text)
        || text.len() > usize::from(area.width)
        || row < i32::from(area.y)
        || row >= i32::from(area.bottom())
    {
        return;
    }
    let length = text.len() as i32;
    let start = (center.floor() as i32 - length / 2)
        .clamp(i32::from(area.x), i32::from(area.right()) - length);
    for (offset, glyph) in text.chars().enumerate() {
        if let Some(cell) = buf.cell_mut(((start + offset as i32) as u16, row as u16)) {
            cell.set_char(glyph)
                .set_fg(colors.ink(7))
                .set_bg(colors.ink(6));
        }
    }
}

pub struct SpriteWidget<'a> {
    pub sprite: &'a Sprite,
    pub renderer: Renderer,
    pub scale: u16,
    pub colors: &'a Colors,
}

impl Widget for SpriteWidget<'_> {
    fn render(self, area: Rect, buf: &mut Buffer) {
        let area = area.intersection(buf.area);
        let Some(scale) = fit_scale(
            self.sprite.width(),
            self.sprite.height(),
            area.width,
            area.height,
            self.renderer,
            self.scale,
        ) else {
            Paragraph::new("Terminal too small").render(area, buf);
            return;
        };
        let (columns, rows) = cell_size(
            self.sprite.width(),
            self.sprite.height(),
            scale,
            self.renderer,
        );
        let (dx, dy) = self.renderer.packing();
        let left = u32::from(area.x) + (u32::from(area.width) - columns) / 2;
        let top = u32::from(area.y) + (u32::from(area.height) - rows) / 2;
        let projection = Projection {
            left: f64::from(left) * f64::from(dx),
            top: f64::from(top) * f64::from(dy),
            scale: f64::from(scale),
            scale_y: f64::from(scale),
        };
        draw_projection(
            self.sprite,
            self.renderer,
            self.colors,
            projection,
            area,
            buf,
        );
        if !draw_front_face(
            self.sprite,
            self.renderer,
            self.colors,
            projection,
            area,
            buf,
        ) {
            if let (Some(face), Some(bounds)) =
                (self.sprite.face_overlay(), SolidBounds::of(self.sprite))
            {
                let (center, bottom) = projection.cell(
                    (bounds.left + bounds.right) / 2.0,
                    bounds.bottom,
                    self.renderer,
                );
                draw_caption(
                    face.text,
                    center,
                    bottom.ceil() as i32,
                    area,
                    self.colors,
                    buf,
                );
            }
        }
    }
}

/// Plain, bounded ASCII output. No terminal initialization, escape sequences, or hidden input reads.
pub fn snapshot(sprite: &Sprite, config: &Config) -> Result<String, String> {
    let area = Rect::new(0, 0, sprite.width(), sprite.height());
    let mut buffer = Buffer::empty(area);
    let colors = Colors::from_config(config, Depth::Mono)?;
    SpriteWidget {
        sprite,
        renderer: Renderer::Ascii,
        scale: 1,
        colors: &colors,
    }
    .render(area, &mut buffer);
    let mut text = String::new();
    for y in 0..area.height {
        for x in 0..area.width {
            text.push_str(buffer[(x, y)].symbol());
        }
        text.push('\n');
    }
    Ok(text)
}

/// A complete room canvas. All colors come from the layered sprite palette.
/// Decorations and reactions are composed behind/around the transparent pet.
#[derive(Clone, Copy)]
pub struct HabitatView<'a> {
    pub state: &'a crate::habitat::Habitat,
    pub rules: &'a crate::habitat::HabitatRules,
    pub wins: u32,
    pub age: u64,
}

pub struct RoomWidget<'a> {
    pub world: Option<&'a crate::world::WorldRules>,
    pub sprite: &'a Sprite,
    pub detail: SpriteStyle,
    pub renderer: Renderer,
    pub scale: u16,
    pub colors: &'a Colors,
    pub expression: crate::sprite::Expression,
    pub frame: usize,
    pub position: (u16, u16),
    pub camera: Camera,
    pub habitat: Option<HabitatView<'a>>,
}

/// Restore compositor-owned cells after either native text or atlas face paint.
/// A packed cell shared with the world has multiple colour planes; repainting
/// just its visible head samples would destroy the foreground object's plane.
fn draw_world_face(
    sprite: &Sprite,
    renderer: Renderer,
    colors: &Colors,
    projection: Projection,
    authority: (Rect, &[bool]),
    buf: &mut Buffer,
) -> bool {
    let (viewport, owned) = authority;
    let mut protected = Vec::new();
    for row in viewport.y..viewport.bottom() {
        for col in viewport.x..viewport.right() {
            let index = usize::from(row - viewport.y) * usize::from(viewport.width)
                + usize::from(col - viewport.x);
            if !owned.get(index).copied().unwrap_or(false) {
                if let Some(cell) = buf.cell((col, row)) {
                    protected.push(((col, row), cell.clone()));
                }
            }
        }
    }
    let painted = draw_camera_face(sprite, renderer, colors, projection, viewport, buf);
    for (position, cell) in protected {
        if let Some(target) = buf.cell_mut(position) {
            *target = cell;
        }
    }
    painted
}

impl RoomWidget<'_> {
    /// One orthographic camera and depth space for the entire voxel habitat.
    fn render_world_room(&self, area: Rect, buf: &mut Buffer, rules: &crate::world::WorldRules) {
        use crate::world::{WorldCamera, WorldFrame};
        let caption = u16::from(area.height >= 4);
        let viewport = Rect::new(
            area.x,
            area.y,
            area.width,
            area.height.saturating_sub(caption),
        );
        if viewport.width == 0 || viewport.height == 0 {
            return;
        }
        let (dx, dy) = self.renderer.packing();
        let aspect = camera_pixel_aspect(self.renderer);
        let frame = WorldFrame::render(
            rules,
            self.habitat,
            WorldCamera {
                yaw_degrees: self.camera.orbit_degrees,
                zoom_percent: self.camera.zoom_percent,
                position: self.position,
                pixel_aspect: aspect as f32,
                detail: self.detail,
            },
            (
                viewport.width.saturating_mul(dx),
                viewport.height.saturating_mul(dy),
            ),
        );
        let backdrop = self.colors.ink(usize::from(rules.backdrop));
        for row in area.y..area.bottom() {
            for col in area.x..area.right() {
                if let Some(cell) = buf.cell_mut((col, row)) {
                    cell.set_char(' ')
                        .set_bg(backdrop)
                        .set_fg(self.colors.ink(7));
                    cell.modifier = ratatui::style::Modifier::empty();
                }
            }
        }
        let Some(bounds) = SolidBounds::of(self.sprite) else {
            return;
        };
        let scale_y = (f64::from(frame.pet_height_pixels) / bounds.height()).max(0.00001);
        let scale = scale_y / aspect;
        let ground = self
            .sprite
            .ground_pixel()
            .unwrap_or([(bounds.left + bounds.right) / 2.0, bounds.bottom]);
        let projection = Projection {
            left: f64::from(viewport.x) * f64::from(dx) + f64::from(frame.pet_anchor[0])
                - ground[0] * scale,
            top: f64::from(viewport.y) * f64::from(dy) + f64::from(frame.pet_anchor[1])
                - ground[1] * scale_y,
            scale,
            scale_y,
        };
        let depth_at = |sx, sy| {
            f64::from(frame.pet_anchor[2])
                + self.sprite.depth_pixels(sx, sy).unwrap_or(0.0) * scale
                    / f64::from(frame.scale.max(0.00001))
        };
        let sample = |x: u32, y: u32| {
            frame.sample(
                x.saturating_sub(u32::from(viewport.x) * u32::from(dx)),
                y.saturating_sub(u32::from(viewport.y) * u32::from(dy)),
            )
        };
        let mut pet_cells = vec![false; usize::from(viewport.width) * usize::from(viewport.height)];
        for row in viewport.y..viewport.bottom() {
            for col in viewport.x..viewport.right() {
                let mut all_pet = true;
                let mut pixels = [None; 8];
                let mut surface = [None; 8];
                for y in 0..dy {
                    for x in 0..dx {
                        let i = usize::from(y * dx + x);
                        let px = u32::from(col) * u32::from(dx) + u32::from(x);
                        let py = u32::from(row) * u32::from(dy) + u32::from(y);
                        let world = sample(px, py);
                        let mut pet_wins = false;
                        if let Some(w) = world {
                            pixels[i] = Some(w.palette);
                            surface[i] = Some(SurfaceLight {
                                normal: w.normal,
                                occlusion: w.occlusion,
                                edge: false,
                                screen_edge: false,
                                texture: None,
                            });
                        }
                        if let Some((sx, sy)) = projection.source_pixel(self.sprite, px, py) {
                            if let Some(index) = self.sprite.pixel(sx, sy, 1) {
                                if world
                                    .is_none_or(|w| f64::from(w.depth) <= depth_at(sx, sy) + 0.0001)
                                {
                                    pet_wins = true;
                                    pixels[i] = Some(index);
                                    surface[i] =
                                        projected_surface_light(self.sprite, projection, px, py);
                                }
                            }
                        }
                        all_pet &= pet_wins;
                    }
                }
                pet_cells[usize::from(row - viewport.y) * usize::from(viewport.width)
                    + usize::from(col - viewport.x)] = all_pet;
                if let Some(cell) = buf.cell_mut((col, row)) {
                    paint_lit_pixels(
                        self.renderer,
                        self.colors,
                        pixels,
                        surface,
                        [false; 8],
                        cell,
                    );
                }
            }
        }
        // Facial text requires an entirely visible source cell. Body samples
        // use exact packed-pixel depth above; this conservative mask prevents
        // a native glyph from painting through a foreground table or lamp.
        let face_sprite = self.sprite.retain_visible_pixels(|sx, sy| {
            let x0 = (projection.left + f64::from(sx) * scale)
                .floor()
                .max(f64::from(viewport.x) * f64::from(dx)) as u32;
            let x1 = (projection.left + f64::from(sx + 1) * scale)
                .ceil()
                .min(f64::from(viewport.right()) * f64::from(dx))
                .max(0.0) as u32;
            let y0 = (projection.top + f64::from(sy) * scale_y)
                .floor()
                .max(f64::from(viewport.y) * f64::from(dy)) as u32;
            let y1 = (projection.top + f64::from(sy + 1) * scale_y)
                .ceil()
                .min(f64::from(viewport.bottom()) * f64::from(dy))
                .max(0.0) as u32;
            if x0 >= x1 || y0 >= y1 {
                return false;
            }
            let depth = depth_at(sx, sy);
            (y0..y1).all(|y| {
                (x0..x1).all(|x| sample(x, y).is_none_or(|w| f64::from(w.depth) <= depth + 0.0001))
            })
        });
        let face_painted = draw_world_face(
            &face_sprite,
            self.renderer,
            self.colors,
            projection,
            (viewport, &pet_cells),
            buf,
        );
        let Some(face) = self.sprite.face_text() else {
            return;
        };
        if caption != 0 {
            draw_caption(
                face,
                f64::from(area.x) + f64::from(area.width) / 2.0,
                i32::from(area.bottom() - 1),
                area,
                self.colors,
                buf,
            );
        } else if !face_painted && printable_face(face) {
            let text = if face.len() <= usize::from(area.width) {
                Some(face)
            } else {
                face.get(1..face.len().saturating_sub(1))
                    .filter(|s| s.len() <= usize::from(area.width))
            };
            if let Some(text) = text {
                let row = area.bottom() - 1;
                for col in area.x..area.right() {
                    if let Some(cell) = buf.cell_mut((col, row)) {
                        cell.set_char(' ')
                            .set_bg(backdrop)
                            .set_fg(self.colors.ink(7));
                    }
                }
                draw_caption(
                    text,
                    f64::from(area.x) + f64::from(area.width) / 2.0,
                    i32::from(row),
                    area,
                    self.colors,
                    buf,
                );
            }
        }
    }
}

impl Widget for RoomWidget<'_> {
    fn render(self, area: Rect, buf: &mut Buffer) {
        use crate::sprite::Expression;
        let area = area.intersection(buf.area);
        if area.width == 0 || area.height == 0 {
            return;
        }
        if let Some(rules) = self
            .world
            .filter(|rules| rules.enabled && self.camera.view == CameraView::Room)
        {
            self.render_world_room(area, buf, rules);
            return;
        }
        let ascii = self.renderer == Renderer::Ascii;
        let floor = area.y + area.height.saturating_mul(3) / 4;
        let theme = self
            .habitat
            .and_then(|v| v.rules.themes.iter().find(|t| t.id == v.state.theme));
        let wall_index = theme.map_or(
            if self.expression == Expression::Sleeping {
                6
            } else {
                10
            },
            |t| usize::from(t.wall),
        );
        let floor_index = theme.map_or(14, |t| usize::from(t.floor));
        let accent = theme.map_or(6, |t| usize::from(t.accent));
        let pattern = theme.map_or(0, |t| t.pattern);
        let wall = self.colors.ink(wall_index);
        let pitch = if self.detail == SpriteStyle::EightBit {
            2
        } else {
            1
        };
        for y in area.y..area.bottom() {
            for x in area.x..area.right() {
                let ground = y >= floor;
                let px = (x - area.x) / pitch;
                let py = (y - area.y) / pitch;
                let bg = if ground {
                    let ground_y = (y - floor) / pitch;
                    let tile = match pattern {
                        1 => px / 5,
                        2 => px / 8 + ground_y / 2,
                        3 => 0,
                        4 => px / 4 + ground_y,
                        5 => px / 7 + ground_y / 3,
                        6 => px / 9 + ground_y,
                        7 => px / 6 + ground_y % 2,
                        _ => 0,
                    };
                    self.colors.ink(if tile.is_multiple_of(2) {
                        floor_index
                    } else {
                        accent
                    })
                } else {
                    wall
                };
                if let Some(cell) = buf.cell_mut((x, y)) {
                    cell.set_char(if !ground && px.is_multiple_of(7) && py % 3 == 1 {
                        if pattern == 6 {
                            '*'
                        } else if pattern == 5 {
                            '\''
                        } else if ascii {
                            '.'
                        } else {
                            '·'
                        }
                    } else {
                        ' '
                    })
                    .set_bg(bg)
                    .set_fg(self.colors.ink(14));
                }
            }
        }
        let mut paint = |rect: Rect, symbol: char, fg: usize, bg: usize| {
            for y in rect.y..rect.bottom() {
                for x in rect.x..rect.right() {
                    if area.contains((x, y).into()) {
                        if let Some(c) = buf.cell_mut((x, y)) {
                            c.set_char(symbol)
                                .set_fg(self.colors.ink(fg))
                                .set_bg(self.colors.ink(bg));
                        }
                    }
                }
            }
        };
        if self.habitat.is_none() && area.width >= 28 && area.height >= 10 {
            let wx = area.x + 3;
            let wy = area.y + 2;
            paint(
                Rect::new(wx, wy, 12, 6),
                if ascii { '#' } else { '▌' },
                7,
                14,
            );
            paint(
                Rect::new(wx + 1, wy + 1, 10, 4),
                ' ',
                13,
                if self.expression == Expression::Sleeping {
                    6
                } else {
                    9
                },
            );
            paint(
                Rect::new(wx + 2, wy + 1, 2, 2),
                if ascii { '*' } else { '▀' },
                4,
                9,
            );
            paint(Rect::new(wx + 6, wy + 2, 3, 1), ' ', 2, 13);
            paint(Rect::new(wx + 5, wy + 1, 1, 4), '|', 7, 14);
            paint(
                Rect::new(wx, wy, 2, 6),
                if ascii { '|' } else { '▒' },
                5,
                10,
            );
            paint(
                Rect::new(wx + 10, wy, 2, 6),
                if ascii { '|' } else { '▒' },
                5,
                10,
            );
            let px = area.right().saturating_sub(10);
            let py = floor.saturating_sub(3);
            paint(
                Rect::new(px + 2, py, 2, 2),
                if ascii { '^' } else { '▄' },
                11,
                10,
            );
            paint(
                Rect::new(px, py + 1, 6, 1),
                if ascii { '^' } else { '▀' },
                15,
                10,
            );
            paint(
                Rect::new(px + 1, py + 2, 4, 2),
                if ascii { '#' } else { '▀' },
                8,
                14,
            );
            paint(Rect::new(area.x + 2, floor + 1, 12, 2), ' ', 5, 5);
            paint(
                Rect::new(area.x + 3, floor, 10, 1),
                if ascii { '_' } else { '▄' },
                4,
                10,
            );
        }
        if let Some(view) = self.habitat {
            draw_habitat(area, buf, self.colors, view, floor, self.detail);
        }
        // Keep a real text row beneath the drawable pet, including when zoom
        // crops its lower body. This is an expression caption, never a rear face.
        let caption_rows = u16::from(area.height >= 4);
        let reaction_rows = u16::from(area.height >= 6);
        let margin = u16::from(area.width >= 12);
        let viewport = Rect::new(
            area.x + margin,
            area.y + reaction_rows,
            area.width.saturating_sub(2 * margin),
            area.height.saturating_sub(caption_rows + reaction_rows),
        );
        let Some(bounds) = SolidBounds::of(self.sprite) else {
            return;
        };
        let projection = Projection::camera(
            self.sprite,
            bounds,
            viewport,
            self.renderer,
            self.scale,
            self.position,
            self.camera,
        );
        let (left, _) = projection.cell(bounds.left, bounds.top, self.renderer);
        let (right, bottom) = projection.cell(bounds.right, bounds.bottom, self.renderer);
        // A floor shadow follows the projected feet; offscreen feet cast no
        // artificial line across a clipped viewport edge.
        let shadow_y = bottom.ceil() as i32;
        if shadow_y >= i32::from(viewport.y) && shadow_y < i32::from(viewport.bottom()) {
            let start = (left.floor() as i32).max(i32::from(viewport.x));
            let end = (right.ceil() as i32).min(i32::from(viewport.right()));
            for x in start..end {
                if let Some(cell) = buf.cell_mut((x as u16, shadow_y as u16)) {
                    cell.set_char(if ascii { '.' } else { '▄' })
                        .set_fg(self.colors.ink(6));
                }
            }
        }
        draw_projection(
            self.sprite,
            self.renderer,
            self.colors,
            projection,
            viewport,
            buf,
        );
        let face_painted = draw_camera_face(
            self.sprite,
            self.renderer,
            self.colors,
            projection,
            viewport,
            buf,
        );
        // Restricted views keep an explicit inline row below the cropped
        // sprite when no complete depth-authorized facial text can fit.
        // This is a caption pane, not permission to paint over a beak or rear.
        if !face_painted && caption_rows == 0 && viewport.height != 0 {
            let face = self
                .sprite
                .face_text()
                .or_else(|| self.sprite.face_overlay().map(|face| face.text));
            if let Some(face) = face.filter(|face| printable_face(face)) {
                let face = if face.len() <= usize::from(viewport.width) {
                    Some(face)
                } else {
                    face.get(1..face.len().saturating_sub(1))
                        .filter(|core| core.len() <= usize::from(viewport.width))
                };
                if let Some(face) = face {
                    let row = viewport.bottom() - 1;
                    for column in viewport.x..viewport.right() {
                        if let Some(cell) = buf.cell_mut((column, row)) {
                            cell.set_char(' ')
                                .set_fg(self.colors.ink(7))
                                .set_bg(self.colors.ink(6));
                            cell.modifier = ratatui::style::Modifier::empty();
                        }
                    }
                    draw_caption(
                        face,
                        f64::from(viewport.x) + f64::from(viewport.width) / 2.0,
                        i32::from(row),
                        viewport,
                        self.colors,
                        buf,
                    );
                }
            }
        }
        if caption_rows != 0 {
            let mood = match self.expression {
                Expression::Idle => "curious",
                Expression::Happy => "sunny",
                Expression::Sad => "blue",
                Expression::Angry => "grumpy",
                Expression::Surprised => "wonder",
                Expression::Sleeping => "dreaming",
                Expression::Sick => "poorly",
                Expression::Hungry => "peckish",
                Expression::Playful => "playful",
                Expression::Shy => "bashful",
                Expression::Excited => "sparkly",
                Expression::Eating => "munching",
                Expression::Cleaning => "splashing",
                Expression::Healing => "mending",
                Expression::Celebrate => "hooray",
                Expression::Dead => "farewell",
            };
            // Metadata is the authority for the animated visible front face.
            // A rear-facing animal still has a plainly separate mood caption.
            let face = self
                .sprite
                .face_overlay()
                .map_or_else(|| self.expression.kaomoji(self.frame), |face| face.text);
            let caption = format!("{face} {mood}");
            let caption = if caption.len() <= usize::from(area.width) {
                caption.as_str()
            } else {
                face
            };
            let center = (left + right) / 2.0;
            let row = shadow_y.clamp(i32::from(viewport.y), i32::from(area.bottom()) - 1);
            draw_caption(caption, center, row, area, self.colors, buf);
        }
        let effect = match self.expression {
            Expression::Eating => {
                if self.frame.is_multiple_of(2) {
                    "nom"
                } else {
                    "munch"
                }
            }
            Expression::Sleeping => {
                if self.frame.is_multiple_of(2) {
                    "z Z"
                } else {
                    "Z z"
                }
            }
            Expression::Cleaning => {
                if self.frame.is_multiple_of(2) {
                    "o . o"
                } else {
                    ". o ."
                }
            }
            Expression::Healing => "+ + +",
            Expression::Celebrate | Expression::Excited => {
                if self.frame.is_multiple_of(2) {
                    "* <3 *"
                } else {
                    "<3 * <3"
                }
            }
            Expression::Happy => "<3",
            Expression::Sick => "+ rest +",
            Expression::Hungry => "snack?",
            Expression::Sad => ". . .",
            Expression::Angry => "! !",
            Expression::Surprised => "!",
            Expression::Playful => {
                if self.frame.is_multiple_of(2) {
                    "*  *"
                } else {
                    " * "
                }
            }
            Expression::Shy => "..",
            Expression::Idle | Expression::Dead => "",
        };
        let effect = if effect.len() <= usize::from(area.width) {
            effect
        } else {
            ""
        };
        let x = area.x + (area.width.saturating_sub(effect.len() as u16)) / 2;
        let y = area.y;
        if area.height <= 3 {
            return;
        }
        for (offset, ch) in effect.chars().enumerate() {
            if let Some(cell) = buf.cell_mut((x + offset as u16, y)) {
                cell.set_char(ch).set_fg(self.colors.ink(7)).set_bg(wall);
            }
        }
    }
}

#[cfg(test)]
mod voxel_lighting_tests {
    use super::*;
    use crate::{
        game::Stage,
        sprite::{Appearance, Expression, SpriteStyle},
    };

    fn light() -> crate::lighting::VoxelLighting {
        let mut light = Config::sample().unwrap().voxel_lighting;
        light.enabled = true;
        light
    }

    fn surface(normal: [f32; 3], occlusion: u8, edge: bool) -> Option<SurfaceLight> {
        Some(SurfaceLight {
            normal,
            occlusion,
            edge,
            screen_edge: false,
            texture: None,
        })
    }

    #[test]
    fn halfblock_uses_independent_directional_contact_and_outline_shading() {
        let light = light();
        let base = [120, 80, 40];
        let colors = Colors {
            palette: vec![
                Color::Rgb(12, 16, 24),
                Color::Rgb(base[0], base[1], base[2]),
            ],
            background: Color::Rgb(1, 2, 3),
            lighting: Some(light.clone()),
        };
        let up = surface([0.0, 1.0, 0.0], 0, false);
        let down = surface([0.0, -1.0, 0.0], 100, true);
        let bright = light.brightness([0.0, 1.0, 0.0], 0, false);
        let dark = light.brightness([0.0, -1.0, 0.0], 100, true);
        assert!(
            bright > dark,
            "direction, contact and edge must produce distinct illumination"
        );
        let mut cell = ratatui::buffer::Cell::default();
        paint_lit_pixels(
            Renderer::HalfBlock,
            &colors,
            [Some(1), Some(1), None, None, None, None, None, None],
            [up, down, None, None, None, None, None, None],
            [false; 8],
            &mut cell,
        );
        let upper = light.shade_rgb(base, bright);
        let lower = light.shade_rgb(base, dark);
        assert_eq!(cell.fg, Color::Rgb(upper[0], upper[1], upper[2]));
        assert_eq!(cell.bg, Color::Rgb(lower[0], lower[1], lower[2]));
        assert_eq!(cell.symbol(), "▀");
        assert!(
            upper[0] >= upper[1] && upper[1] >= upper[2],
            "skin channel order must survive"
        );
        assert!(light.brightness([0.0, 1.0, 0.0], 100, false) < bright);
        assert!(light.brightness([0.0, 1.0, 0.0], 0, true) < bright);
    }

    #[test]
    fn indexed_ansi_and_mono_keep_their_color_contract_and_ascii_tones() {
        let light = light();
        let normal = [0.0, -1.0, 0.0];
        let brightness = light.brightness(normal, 50, false);
        for base in [Color::Indexed(208), Color::Yellow, Color::Reset] {
            let colors = Colors {
                palette: vec![base],
                background: Color::Reset,
                lighting: Some(light.clone()),
            };
            let mut cell = ratatui::buffer::Cell::default();
            paint_lit_pixels(
                Renderer::Ascii,
                &colors,
                [Some(0), None, None, None, None, None, None, None],
                [
                    surface(normal, 50, false),
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                ],
                [false; 8],
                &mut cell,
            );
            assert_eq!(cell.symbol(), light.glyph(brightness).to_string());
            assert!(cell.symbol().is_ascii());
            let expected = match base {
                Color::Reset => Color::Reset,
                Color::Indexed(_) => quantize(
                    light.shade_rgb(terminal_rgb(base).unwrap(), brightness),
                    Depth::Indexed,
                ),
                _ => quantize(
                    light.shade_rgb(terminal_rgb(base).unwrap(), brightness),
                    Depth::Ansi,
                ),
            };
            assert_eq!(cell.fg, expected);
        }
        assert_eq!(terminal_rgb(Color::Indexed(16)), Some([0, 0, 0]));
        assert_eq!(terminal_rgb(Color::Indexed(196)), Some([255, 0, 0]));
        assert_eq!(terminal_rgb(Color::Indexed(232)), Some([8; 3]));
        assert_eq!(terminal_rgb(Color::Indexed(255)), Some([238; 3]));
    }

    #[test]
    fn lit_patterns_keep_occupancy_and_background_and_face_ink_stays_flat() {
        let colors = Colors {
            palette: vec![Color::Rgb(12, 16, 24), Color::Rgb(120, 80, 40)],
            background: Color::Rgb(1, 2, 3),
            lighting: Some(light()),
        };
        let pixels = [Some(1), None, None, Some(1), None, Some(1), None, None];
        for renderer in [Renderer::Quadrant, Renderer::Braille] {
            let mut plain = ratatui::buffer::Cell::default();
            plain.set_bg(Color::Rgb(9, 8, 7));
            let mut lit = plain.clone();
            paint_pixels(renderer, &colors, pixels, &mut plain);
            paint_lit_pixels(
                renderer,
                &colors,
                pixels,
                [surface([0.0, -1.0, 0.0], 100, true); 8],
                [false; 8],
                &mut lit,
            );
            assert_eq!(
                lit.symbol(),
                plain.symbol(),
                "lighting cannot fill transparent subpixels"
            );
            assert_eq!(
                lit.bg, plain.bg,
                "lighting cannot create a terminal background badge"
            );
            assert_ne!(lit.fg, plain.fg);
        }
        let mut cell = ratatui::buffer::Cell::default();
        let pixels = [Some(0), Some(1), None, None, None, None, None, None];
        let surface = [surface([0.0, 1.0, 0.0], 0, false); 8];
        let mut ink = [false; 8];
        ink[0] = true;
        paint_lit_pixels(
            Renderer::HalfBlock,
            &colors,
            pixels,
            surface,
            ink,
            &mut cell,
        );
        assert_eq!(
            cell.fg,
            colors.ink(0),
            "head shading must never dim or brighten expression ink"
        );
        let mut unlit = colors.clone();
        unlit.lighting = None;
        let mut expected = ratatui::buffer::Cell::default();
        let mut actual = expected.clone();
        paint_pixels(Renderer::HalfBlock, &unlit, pixels, &mut expected);
        paint_lit_pixels(
            Renderer::HalfBlock,
            &unlit,
            pixels,
            surface,
            ink,
            &mut actual,
        );
        assert_eq!(
            actual, expected,
            "synthetic and legacy unlit packing must remain exact"
        );
    }

    #[test]
    fn actual_lit_voxel_projection_is_clipped_and_native_face_keeps_dark_contrast() {
        let sprite = Sprite::voxel_with_appearance(
            Stage::Child,
            Expression::Sick,
            SpriteStyle::SixteenBit,
            1,
            0,
            false,
            Appearance {
                shape: 14,
                body: 9,
                accent: 6,
            },
        );
        let config = Config::sample().unwrap();
        for depth in [Depth::Rgb, Depth::Indexed, Depth::Mono] {
            let colors = Colors::from_config(&config, depth).unwrap();
            for renderer in [
                Renderer::Ascii,
                Renderer::HalfBlock,
                Renderer::Quadrant,
                Renderer::Braille,
            ] {
                let area = Rect::new(3, 4, 30, 12);
                let projection = Projection::camera(
                    &sprite,
                    SolidBounds::of(&sprite).unwrap(),
                    area,
                    renderer,
                    0,
                    (500, 500),
                    Camera::default(),
                );
                let mut before = Buffer::empty(Rect::new(0, 0, 40, 24));
                for cell in &mut before.content {
                    cell.set_char('X')
                        .set_fg(Color::Rgb(1, 2, 3))
                        .set_bg(Color::Rgb(9, 8, 7));
                }
                let outside = before.clone();
                draw_projection(&sprite, renderer, &colors, projection, area, &mut before);
                let mut after = before.clone();
                draw_camera_face(&sprite, renderer, &colors, projection, area, &mut after);
                let mut face_cells = 0;
                for y in after.area.y..after.area.bottom() {
                    for x in after.area.x..after.area.right() {
                        if !area.contains((x, y).into()) {
                            assert_eq!(after[(x, y)], outside[(x, y)]);
                        } else if after[(x, y)] != before[(x, y)] {
                            assert_eq!(
                                after[(x, y)].fg,
                                face_ink(&sprite, &colors, InkRegion::NativeBand)
                            );
                            assert_eq!(after[(x, y)].bg, before[(x, y)].bg);
                            face_cells += 1;
                        }
                    }
                }
                assert!(
                    face_cells >= 3,
                    "{depth:?} {renderer:?}: actual bird needs native eyes and mouth"
                );
            }
        }
    }
}

#[cfg(test)]
mod face_tests {
    use super::*;
    use crate::{
        game::Stage,
        sprite::{Appearance, Expression, SpriteStyle},
    };

    #[test]
    fn literal_kaomoji_is_in_headless_and_graphical_front_faces_but_not_rear_views() {
        let config = Config::sample().unwrap();
        let colors = Colors::from_config(&config, Depth::Indexed).unwrap();
        for shape in 0..16 {
            let appearance = Appearance {
                shape,
                body: 1,
                accent: 11,
            };
            let front = Sprite::voxel_with_appearance(
                Stage::Adult,
                Expression::Happy,
                SpriteStyle::SixteenBit,
                0,
                0,
                false,
                appearance,
            );
            let expected = Expression::Happy.kaomoji(0);
            assert!(
                snapshot(&front, &config).unwrap().contains(expected),
                "shape {shape}"
            );
            let area = Rect::new(2, 2, front.width(), front.height().div_ceil(2));
            let mut buffer = Buffer::empty(Rect::new(0, 0, area.right() + 2, area.bottom() + 2));
            SpriteWidget {
                sprite: &front,
                renderer: Renderer::HalfBlock,
                scale: 1,
                colors: &colors,
            }
            .render(area, &mut buffer);
            let text: String = buffer.content.iter().map(|c| c.symbol()).collect();
            assert!(text.contains(expected), "shape {shape}");
            let rear = Sprite::voxel_with_appearance(
                Stage::Adult,
                Expression::Happy,
                SpriteStyle::SixteenBit,
                0,
                180,
                false,
                appearance,
            );
            assert!(!snapshot(&rear, &config).unwrap().contains(expected));
        }
    }

    #[test]
    fn scaled_faces_never_escape_the_pet_or_buffer_and_tiny_views_remain_bounded() {
        let config = Config::sample().unwrap();
        let colors = Colors::from_config(&config, Depth::Mono).unwrap();
        let sprite = Sprite::voxel(
            Stage::Adult,
            Expression::Idle,
            SpriteStyle::SixteenBit,
            0,
            0,
            false,
        );
        for renderer in [
            Renderer::Ascii,
            Renderer::HalfBlock,
            Renderer::Quadrant,
            Renderer::Braille,
        ] {
            for (width, height) in [(1, 1), (3, 2), (10, 4), (40, 40), (80, 80)] {
                let mut buffer = Buffer::empty(Rect::new(0, 0, 100, 100));
                let compact = sprite.downsample_to_fit(width, height);
                SpriteWidget {
                    sprite: &compact,
                    renderer,
                    scale: 0,
                    colors: &colors,
                }
                .render(Rect::new(2, 2, width, height), &mut buffer);
                assert!(buffer
                    .content
                    .iter()
                    .all(|c| !c.symbol().contains('\u{1b}')));
                assert_eq!(buffer[(0, 0)].symbol(), " ");
                assert_eq!(buffer[(99, 99)].symbol(), " ");
            }
        }
    }
}

fn draw_habitat(
    area: Rect,
    buf: &mut Buffer,
    colors: &Colors,
    view: HabitatView<'_>,
    floor: u16,
    detail: SpriteStyle,
) {
    if area.width < 3 || area.height < 2 {
        return;
    }
    let Some(theme) = view.rules.themes.iter().find(|t| t.id == view.state.theme) else {
        return;
    };
    // A seeded structural window belongs to the room, never to paid inventory.
    if area.width >= 12 && area.height >= 6 {
        let (nx, _) = view.state.layout(15);
        let rows: &[&str] = if detail == SpriteStyle::EightBit {
            &["[ ]", "[_]"]
        } else {
            &["+---+", "| . |", "+---+"]
        };
        let width = rows[0].len() as u16;
        let x = area.x + ((u32::from(area.width - width) * u32::from(nx.min(1000))) / 1000) as u16;
        for (dy, row) in rows.iter().enumerate() {
            for (dx, glyph) in row.chars().enumerate() {
                if let Some(c) = buf.cell_mut((x + dx as u16, area.y + 1 + dy as u16)) {
                    c.set_char(glyph)
                        .set_fg(colors.ink(usize::from(theme.accent)))
                        .set_bg(colors.ink(usize::from(theme.wall)));
                }
            }
        }
    }
    let art: [&[&str]; 8] = [
        &[" ^Y^ ", "  |  ", " [_] "],
        &[" ___ ", " | | ", " | | "],
        &[" ___ ", "|___|", " | | "],
        &[" .-. ", " |o| ", "  |  "],
        &[".....", ":===:", "....."],
        &["[_][_]", "|___|", "|___|"],
        &["[~~~]", "[ * ]", "[___]"],
        &[" [_] ", "  |  ", " === "],
    ];
    for item in &view.rules.items {
        let Some(bit) = 1u64.checked_shl(u32::from(item.id)) else {
            continue;
        };
        if view.state.placed & bit == 0
            || !view.state.owns(item.id, view.wins, view.age, view.rules)
        {
            continue;
        }
        let kind = usize::from(item.kind).min(7);
        let tiny = ["Y", "b", "T", "*", "=", "#", "+", "!"];
        let rows: &[&str] = if detail == SpriteStyle::EightBit || area.height < 5 || area.width < 8
        {
            &tiny[kind..=kind]
        } else {
            art[kind]
        };
        let height = rows.len() as u16;
        let width = rows
            .iter()
            .map(|s| s.len() as u16)
            .max()
            .unwrap_or(1)
            .min(area.width);
        let (nx, ny) = view.state.layout(item.slot);
        let x = area.x
            + ((u32::from(area.width.saturating_sub(width)) * u32::from(nx.min(1000))) / 1000)
                as u16;
        let start = floor.saturating_sub(height.saturating_sub(1)).max(area.y);
        let available = area.bottom().saturating_sub(height).saturating_sub(start);
        let y = start + ((u32::from(available) * u32::from(ny.min(1000))) / 1000) as u16;
        for (dy, row) in rows.iter().enumerate() {
            for (dx, glyph) in row.chars().enumerate() {
                let pos = (x.saturating_add(dx as u16), y.saturating_add(dy as u16));
                if glyph != ' ' && area.contains(pos.into()) {
                    if let Some(c) = buf.cell_mut(pos) {
                        c.set_char(glyph)
                            .set_fg(colors.ink(usize::from(theme.accent)));
                    }
                }
            }
        }
    }
}

#[cfg(test)]
mod habitat_tests {
    use super::*;
    use crate::{
        game::{Game, Stage},
        sprite::{Expression, SpriteStyle},
    };

    fn room(game: &Game, config: &Config) -> Buffer {
        let sprite = Sprite::voxel(
            Stage::Baby,
            Expression::Idle,
            SpriteStyle::EightBit,
            0,
            0,
            false,
        );
        let colors = Colors::from_config(config, Depth::Indexed).unwrap();
        let mut buffer = Buffer::empty(Rect::new(0, 0, 90, 34));
        RoomWidget {
            world: None,
            detail: SpriteStyle::EightBit,
            sprite: &sprite,
            renderer: Renderer::HalfBlock,
            scale: 0,
            colors: &colors,
            expression: Expression::Idle,
            frame: 0,
            position: (500, 700),
            camera: Camera::default(),
            habitat: Some(HabitatView {
                state: &game.habitat,
                rules: &config.habitat,
                wins: game.wins,
                age: game.age_ticks,
            }),
        }
        .render(Rect::new(1, 1, 88, 32), &mut buffer);
        buffer
    }

    #[test]
    fn global_detail_changes_room_patterns_and_items_without_changing_game_or_habitat() {
        let config = Config::sample().unwrap();
        let mut game = Game::new(&config.game, 42);
        let theme = config
            .habitat
            .themes
            .iter()
            .find(|t| t.pattern == 2)
            .expect("sample habitat must exercise a tiled theme");
        game.habitat.theme = theme.id;
        game.buy_item(0, &config.habitat).unwrap();
        game.toggle_item(0, &config.habitat).unwrap();
        assert_ne!(
            game.habitat.placed, 0,
            "the detail gate must include an actually placed item"
        );
        let before = serde_json::to_vec(&game).unwrap();
        let visual_seed = game.visual_seed();
        let colors = Colors::from_config(&config, Depth::Rgb).unwrap();
        let area = Rect::new(1, 1, 80, 26);
        let render = |detail| {
            let sprite = Sprite::voxel(Stage::Child, Expression::Idle, detail, 0, 0, false);
            let mut buf = Buffer::empty(Rect::new(0, 0, 82, 28));
            RoomWidget {
                world: None,
                detail,
                sprite: &sprite,
                renderer: Renderer::HalfBlock,
                scale: 0,
                colors: &colors,
                expression: Expression::Idle,
                frame: 0,
                position: (500, 700),
                camera: Camera::default(),
                habitat: Some(HabitatView {
                    state: &game.habitat,
                    rules: &config.habitat,
                    wins: game.wins,
                    age: game.age_ticks,
                }),
            }
            .render(area, &mut buf);
            buf
        };
        let fine = render(SpriteStyle::SixteenBit);
        let coarse = render(SpriteStyle::EightBit);
        assert_ne!(fine, coarse, "global detail must change actual room output");
        let floor = area.y + area.height * 3 / 4;
        assert_eq!(
            fine[(area.x + 8, floor)].bg,
            colors.ink(usize::from(theme.accent))
        );
        assert_eq!(
            coarse[(area.x + 8, floor)].bg,
            colors.ink(usize::from(theme.floor))
        );
        let (nx, _) = game.habitat.layout(15);
        let fine_window_x =
            area.x + ((u32::from(area.width - 5) * u32::from(nx.min(1000))) / 1000) as u16;
        let coarse_window_x =
            area.x + ((u32::from(area.width - 3) * u32::from(nx.min(1000))) / 1000) as u16;
        let x = [area.x, area.x + 14]
            .into_iter()
            .find(|x| {
                !(*x >= fine_window_x && *x < fine_window_x + 5)
                    && !(*x >= coarse_window_x && *x < coarse_window_x + 3)
            })
            .unwrap();
        assert_eq!(fine[(x, area.y + 2)].symbol(), " ");
        assert_eq!(coarse[(x, area.y + 2)].symbol(), "·");
        assert_eq!(
            coarse[(x + 1, area.y + 2)].symbol(),
            "·",
            "eight-bit wall motifs must occupy a coarser two-cell pitch"
        );
        assert_eq!(before, serde_json::to_vec(&game).unwrap());
        assert_eq!(visual_seed, game.visual_seed());
        assert_eq!(fine[(0, 0)].symbol(), " ");
        assert_eq!(coarse[(81, 27)].symbol(), " ");
    }

    #[test]
    fn seeded_themes_are_stable_and_equipping_changes_only_cosmetic_room_state() {
        let config = Config::sample().unwrap();
        let mut game = Game::new(&config.game, 7);
        let baseline = room(&game, &config);
        assert_eq!(baseline, room(&game, &config));
        let other = Game::new(&config.game, 19);
        assert_ne!(baseline, room(&other, &config));
        let seed = game.habitat.seed;
        let rng = game.visual_seed();
        game.buy_item(0, &config.habitat).unwrap();
        game.toggle_item(0, &config.habitat).unwrap();
        assert_ne!(baseline, room(&game, &config));
        game.toggle_item(0, &config.habitat).unwrap();
        assert_eq!(baseline, room(&game, &config));
        assert_eq!(game.habitat.seed, seed);
        assert_eq!(game.visual_seed(), rng);
    }
}

#[cfg(test)]
mod camera_tests {
    use super::*;
    use crate::{
        game::{Game, Stage},
        sprite::{Appearance, Expression, SpriteStyle},
    };

    fn padded_body() -> Sprite {
        let mut pixels = vec![None; 64 * 64];
        for y in 20..44 {
            for x in 27..37 {
                pixels[y * 64 + x] = Some(if x < 32 { 1 } else { 11 });
            }
        }
        Sprite::new(64, 64, pixels).unwrap()
    }

    fn projected(sprite: &Sprite, renderer: Renderer, camera: Camera, area: Rect) -> Buffer {
        let config = Config::sample().unwrap();
        let colors = Colors::from_config(&config, Depth::Indexed).unwrap();
        let mut buf = Buffer::empty(area);
        let projection = Projection::camera(
            sprite,
            SolidBounds::of(sprite).unwrap(),
            area,
            renderer,
            0,
            (500, 500),
            camera,
        );
        draw_projection(sprite, renderer, &colors, projection, area, &mut buf);
        buf
    }

    fn occupied(buf: &Buffer) -> usize {
        buf.content.iter().filter(|c| c.symbol() != " ").count()
    }

    fn text(buf: &Buffer, area: Rect) -> String {
        let mut result = String::new();
        for y in area.y..area.bottom() {
            for x in area.x..area.right() {
                result.push_str(buf[(x, y)].symbol());
            }
            result.push('\n');
        }
        result
    }

    #[test]
    fn content_fit_uses_the_viewport_and_zoom_changes_actual_rendered_occupancy() {
        let sprite = padded_body();
        let area = Rect::new(0, 0, 80, 24);
        for renderer in [
            Renderer::Ascii,
            Renderer::HalfBlock,
            Renderer::Quadrant,
            Renderer::Braille,
        ] {
            let frames: Vec<_> = [100, 125, 150, 200]
                .into_iter()
                .map(|zoom_percent| {
                    projected(
                        &sprite,
                        renderer,
                        Camera {
                            zoom_percent,
                            ..Camera::default()
                        },
                        area,
                    )
                })
                .collect();
            let counts: Vec<_> = frames.iter().map(occupied).collect();
            assert!(
                counts.windows(2).all(|w| w[1] > w[0]),
                "{renderer:?}: {counts:?}"
            );
            assert!(counts[3] * 100 >= counts[0] * 120);
            assert!(frames.windows(2).all(|w| w[0] != w[1]));
            assert_eq!(
                frames[0],
                projected(&sprite, renderer, Camera::default(), area)
            );
            // At 100%, the padded source's actual body fills almost all height.
            let rows = (0..area.height)
                .filter(|&y| (0..area.width).any(|x| frames[0][(x, y)].symbol() != " "))
                .count();
            assert!(rows >= 22, "{renderer:?}: {rows}");
        }
    }

    #[test]
    fn face_closeup_fits_head_not_tail_or_torso_across_species_and_renderers() {
        for shape in 0..16 {
            for stage in [Stage::Baby, Stage::Child, Stage::Adult] {
                let sprite = Sprite::voxel_with_appearance(
                    stage,
                    Expression::Happy,
                    SpriteStyle::SixteenBit,
                    2,
                    0,
                    false,
                    Appearance {
                        shape,
                        body: 1,
                        accent: 11,
                    },
                );
                let Some(_) = head_surface_bounds(&sprite) else {
                    panic!("shape{shape} {stage:?} must expose its real front head");
                };
                let bounds = SolidBounds::of(&sprite).unwrap();
                // Model an arbitrarily wide tail and long torso in the solid
                // bounds. The visible mapped head remains exactly the same.
                let extended = SolidBounds {
                    left: bounds.left - 100.0,
                    right: bounds.right + 100.0,
                    bottom: bounds.bottom + 100.0,
                    ..bounds
                };
                for renderer in [
                    Renderer::Ascii,
                    Renderer::HalfBlock,
                    Renderer::Quadrant,
                    Renderer::Braille,
                ] {
                    for area in [Rect::new(3, 2, 20, 8), Rect::new(3, 2, 80, 24)] {
                        let projection = |solid, view| {
                            Projection::camera(
                                &sprite,
                                solid,
                                area,
                                renderer,
                                0,
                                (500, 500),
                                Camera {
                                    view,
                                    from_view: view,
                                    ..Camera::default()
                                },
                            )
                        };
                        let normal = projection(bounds, CameraView::Face);
                        let tail = projection(extended, CameraView::Face);
                        for (a, b) in [
                            (normal.scale, tail.scale),
                            (normal.scale_y, tail.scale_y),
                            (normal.left, tail.left),
                            (normal.top, tail.top),
                        ] {
                            assert!(
                                (a - b).abs() < 0.000001,
                                "{renderer:?} shape{shape} {stage:?}: tail changed face framing"
                            );
                            assert!(a.is_finite());
                        }
                        // A control that must respond to whole-body bounds:
                        // fitting the Mon really does include its body/tail.
                        assert!(
                            projection(extended, CameraView::Pet).scale
                                < projection(bounds, CameraView::Pet).scale
                        );
                    }
                }
            }
        }
    }

    #[test]
    fn room_wanders_but_pet_tracks_and_view_blends_have_intermediate_frames() {
        let sprite = padded_body();
        let bounds = SolidBounds::of(&sprite).unwrap();
        let area = Rect::new(3, 2, 80, 24);
        let renderer = Renderer::HalfBlock;
        let project = |view, position, transition_percent| {
            Projection::camera(
                &sprite,
                bounds,
                area,
                renderer,
                0,
                position,
                Camera {
                    view,
                    from_view: CameraView::Room,
                    transition_percent,
                    ..Camera::default()
                },
            )
        };
        let room_left = project(CameraView::Room, (0, 0), 100);
        let room_right = project(CameraView::Room, (1000, 1000), 100);
        assert!(room_right.left > room_left.left && room_right.top > room_left.top);
        let pet_left = project(CameraView::Pet, (0, 0), 100);
        let pet_right = project(CameraView::Pet, (1000, 1000), 100);
        assert_eq!(
            (pet_left.left, pet_left.top),
            (pet_right.left, pet_right.top)
        );
        let frames: Vec<_> = [0, 25, 65, 100]
            .into_iter()
            .map(|transition_percent| {
                projected(
                    &sprite,
                    renderer,
                    Camera {
                        orbit_degrees: 45,
                        view: CameraView::Face,
                        from_view: CameraView::Room,
                        transition_percent,
                        ..Camera::default()
                    },
                    area,
                )
            })
            .collect();
        assert!(frames.windows(2).all(|w| w[0] != w[1]));
        let from = project(CameraView::Face, (500, 500), 0);
        let middle = project(CameraView::Face, (500, 500), 50);
        let to = project(CameraView::Face, (500, 500), 100);
        assert!(from.scale < middle.scale && middle.scale < to.scale);
        assert!((middle.top - (from.top + to.top) / 2.0).abs() < 0.0001);
    }

    #[test]
    fn signed_offscreen_projection_and_tiny_rooms_cannot_write_outside_the_rect() {
        let sprite = padded_body();
        let config = Config::sample().unwrap();
        let colors = Colors::from_config(&config, Depth::Mono).unwrap();
        let buffer_area = Rect::new(2, 3, 60, 60);
        for renderer in [
            Renderer::Ascii,
            Renderer::HalfBlock,
            Renderer::Quadrant,
            Renderer::Braille,
        ] {
            for (width, height) in [
                (0, 0),
                (1, 1),
                (3, 2),
                (10, 2),
                (12, 6),
                (20, 8),
                (40, 12),
                (30, 40),
                (40, 50),
            ] {
                let area = Rect::new(5, 7, width, height);
                for view in [CameraView::Room, CameraView::Pet, CameraView::Face] {
                    let mut buf = Buffer::empty(buffer_area);
                    for cell in &mut buf.content {
                        cell.set_char('!');
                    }
                    let baseline = buf.clone();
                    RoomWidget {
                        world: None,
                        detail: SpriteStyle::SixteenBit,
                        sprite: &sprite,
                        renderer,
                        scale: 0,
                        colors: &colors,
                        expression: Expression::Eating,
                        frame: 1,
                        position: (1000, 0),
                        camera: Camera {
                            orbit_degrees: 45,
                            view,
                            zoom_percent: 400,
                            ..Camera::default()
                        },
                        habitat: None,
                    }
                    .render(area, &mut buf);
                    for y in buffer_area.y..buffer_area.bottom() {
                        for x in buffer_area.x..buffer_area.right() {
                            if !area.contains((x, y).into()) {
                                assert_eq!(buf[(x, y)], baseline[(x, y)], "{renderer:?}/{view:?}");
                            }
                        }
                    }
                    assert!(buf.content.iter().all(|c| !c.symbol().contains('\u{1b}')));
                    if renderer == Renderer::Ascii {
                        assert!(buf.content.iter().all(|c| c.symbol().is_ascii()));
                    }
                }
            }
            for origin in [-1_000_000.0, -3.25, 1_000_000.0] {
                let mut buf = Buffer::empty(buffer_area);
                let area = Rect::new(8, 9, 3, 2);
                draw_projection(
                    &sprite,
                    renderer,
                    &colors,
                    Projection {
                        left: origin,
                        top: origin,
                        scale: 4.0,
                        scale_y: 4.0 * camera_pixel_aspect(renderer),
                    },
                    area,
                    &mut buf,
                );
                for y in buffer_area.y..buffer_area.bottom() {
                    for x in buffer_area.x..buffer_area.right() {
                        if !area.contains((x, y).into()) {
                            assert_eq!(buf[(x, y)].symbol(), " ");
                        }
                    }
                }
            }
        }
    }

    #[test]
    fn face_camera_draws_metadata_on_front_head_and_never_on_the_rear() {
        let config = Config::sample().unwrap();
        let colors = Colors::from_config(&config, Depth::Indexed).unwrap();
        let area = Rect::new(2, 2, 80, 24);
        for shape in 0..16 {
            for frame in [0, 1, 2, 3] {
                let make = |yaw| {
                    Sprite::voxel_with_appearance(
                        Stage::Adult,
                        Expression::Eating,
                        SpriteStyle::SixteenBit,
                        frame,
                        yaw,
                        false,
                        Appearance {
                            shape,
                            body: 1,
                            accent: 11,
                        },
                    )
                };
                let front = make(0);
                let rear = make(180);
                let front_face = front.face_overlay().expect("front head metadata");
                let mut buf = Buffer::empty(Rect::new(0, 0, 84, 28));
                let projection = Projection::camera(
                    &front,
                    SolidBounds::of(&front).unwrap(),
                    area,
                    Renderer::HalfBlock,
                    0,
                    (500, 500),
                    Camera {
                        orbit_degrees: 45,
                        view: CameraView::Face,
                        ..Camera::default()
                    },
                );
                draw_projection(
                    &front,
                    Renderer::HalfBlock,
                    &colors,
                    projection,
                    area,
                    &mut buf,
                );
                assert!(
                    draw_front_face(
                        &front,
                        Renderer::HalfBlock,
                        &colors,
                        projection,
                        area,
                        &mut buf,
                    ),
                    "shape {shape} frame {frame}"
                );
                assert!(text(&buf, area).contains(front_face.text));
                let (_, face_y) = projection.cell(
                    f64::from(front_face.center_x) + 0.5,
                    f64::from(front_face.y) + 0.5,
                    Renderer::HalfBlock,
                );
                assert!(text(
                    &buf,
                    Rect::new(area.x, face_y.floor() as u16, area.width, 1)
                )
                .contains(front_face.text));
                assert!(rear.face_overlay().is_none());
                let previous = buf.clone();
                assert!(!draw_front_face(
                    &rear,
                    Renderer::HalfBlock,
                    &colors,
                    projection,
                    area,
                    &mut buf,
                ));
                assert_eq!(buf, previous);
            }
        }
    }

    #[test]
    fn oblique_metadata_paints_only_the_visible_expression_span() {
        let config = Config::sample().unwrap();
        let colors = Colors::from_config(&config, Depth::Indexed).unwrap();
        let area = Rect::new(2, 2, 80, 24);
        for yaw in [45, 315] {
            let sprite = Sprite::voxel(
                Stage::Adult,
                Expression::Happy,
                SpriteStyle::SixteenBit,
                0,
                yaw,
                false,
            );
            let face = sprite.face_overlay().expect("visible oblique face span");
            assert_eq!(face.text.len(), 3);
            assert_ne!(face.text, Expression::Happy.kaomoji(0));
            let mut buf = Buffer::empty(Rect::new(0, 0, 84, 28));
            let projection = Projection::camera(
                &sprite,
                SolidBounds::of(&sprite).unwrap(),
                area,
                Renderer::HalfBlock,
                0,
                (500, 500),
                Camera {
                    orbit_degrees: 45,
                    view: CameraView::Face,
                    ..Camera::default()
                },
            );
            draw_projection(
                &sprite,
                Renderer::HalfBlock,
                &colors,
                projection,
                area,
                &mut buf,
            );
            assert!(
                draw_front_face(
                    &sprite,
                    Renderer::HalfBlock,
                    &colors,
                    projection,
                    area,
                    &mut buf,
                ),
                "yaw {yaw}"
            );
            let rendered = text(&buf, area);
            assert!(rendered.contains(face.text), "yaw {yaw}");
            assert!(!rendered.contains(Expression::Happy.kaomoji(0)));
        }
    }

    #[test]
    fn caption_is_below_the_pet_in_front_and_rear_and_uses_the_actual_mood() {
        let config = Config::sample().unwrap();
        let colors = Colors::from_config(&config, Depth::Mono).unwrap();
        let area = Rect::new(1, 1, 20, 8);
        for yaw in [0, 180] {
            let sprite = Sprite::voxel(
                Stage::Adult,
                Expression::Eating,
                SpriteStyle::SixteenBit,
                1,
                yaw,
                false,
            );
            let mut buf = Buffer::empty(Rect::new(0, 0, 22, 10));
            RoomWidget {
                world: None,
                detail: SpriteStyle::SixteenBit,
                sprite: &sprite,
                renderer: Renderer::Ascii,
                scale: 0,
                colors: &colors,
                expression: Expression::Eating,
                frame: 1,
                position: (500, 500),
                camera: Camera {
                    zoom_percent: 400,
                    ..Camera::default()
                },
                habitat: None,
            }
            .render(area, &mut buf);
            let caption = text(&buf, Rect::new(1, 8, 20, 1));
            assert!(caption.contains("munching"));
            assert!(caption.contains(
                sprite
                    .face_overlay()
                    .map_or(Expression::Eating.kaomoji(1), |f| f.text,)
            ));
        }
    }

    #[test]
    fn rendering_leaves_game_rng_and_serialized_save_state_unchanged() {
        let config = Config::sample().unwrap();
        let game = Game::new(&config.game, 42);
        let before = serde_json::to_vec(&game).unwrap();
        let visual_seed = game.visual_seed();
        let colors = Colors::from_config(&config, Depth::Mono).unwrap();
        let sprite = Sprite::voxel(
            Stage::Adult,
            Expression::Idle,
            SpriteStyle::SixteenBit,
            2,
            0,
            false,
        );
        for view in [CameraView::Room, CameraView::Pet, CameraView::Face] {
            for zoom_percent in [50, 100, 175, 400] {
                let area = Rect::new(0, 0, 40, 12);
                let mut buf = Buffer::empty(area);
                RoomWidget {
                    world: None,
                    detail: SpriteStyle::SixteenBit,
                    sprite: &sprite,
                    renderer: Renderer::Ascii,
                    scale: 0,
                    colors: &colors,
                    expression: Expression::Idle,
                    frame: 2,
                    position: (350, 750),
                    camera: Camera {
                        view,
                        zoom_percent,
                        ..Camera::default()
                    },
                    habitat: Some(HabitatView {
                        state: &game.habitat,
                        rules: &config.habitat,
                        wins: game.wins,
                        age: game.age_ticks,
                    }),
                }
                .render(area, &mut buf);
                assert!(text(&buf, area).is_ascii());
            }
        }
        assert!(snapshot(&sprite, &config).unwrap().is_ascii());
        assert_eq!(before, serde_json::to_vec(&game).unwrap());
        assert_eq!(visual_seed, game.visual_seed());
    }

    #[test]
    fn mapped_micro_faces_use_real_head_cells_and_keep_readable_cores_where_they_fit() {
        let config = Config::sample().unwrap();
        let colors = Colors::from_config(&config, Depth::Mono).unwrap();
        let mut readable_two_row_cases = 0;
        for renderer in [Renderer::Ascii, Renderer::HalfBlock] {
            for height in [2, 3] {
                let area = Rect::new(2, 3, 10, height);
                for shape in 0..16 {
                    let sprite = Sprite::voxel_with_appearance(
                        Stage::Adult,
                        Expression::Idle,
                        SpriteStyle::SixteenBit,
                        2,
                        0,
                        false,
                        Appearance {
                            shape,
                            body: 1,
                            accent: 11,
                        },
                    );
                    let canonical = sprite.face_text().expect("mapped front expression");
                    let core = &canonical[1..4];
                    let camera = Camera {
                        orbit_degrees: 45,
                        view: CameraView::Face,
                        ..Camera::default()
                    };
                    let projection = Projection::camera(
                        &sprite,
                        SolidBounds::of(&sprite).unwrap(),
                        area,
                        renderer,
                        0,
                        (500, 500),
                        camera,
                    );
                    let mut buf = Buffer::empty(Rect::new(0, 0, 14, 8));
                    RoomWidget {
                        world: None,
                        detail: SpriteStyle::SixteenBit,
                        sprite: &sprite,
                        renderer,
                        scale: 0,
                        colors: &colors,
                        expression: Expression::Idle,
                        frame: 0,
                        position: (500, 500),
                        camera,
                        habitat: None,
                    }
                    .render(area, &mut buf);
                    let (dx, dy) = renderer.packing();
                    let mut marks = Vec::new();
                    for row in area.y..area.bottom() {
                        for col in area.x..area.right() {
                            let cell = &buf[(col, row)];
                            if cell.modifier.contains(ratatui::style::Modifier::BOLD) {
                                for y in 0..dy {
                                    for x in 0..dx {
                                        assert!(
                                            projected_head_uv(
                                                &sprite,
                                                projection,
                                                u32::from(col) * u32::from(dx) + u32::from(x),
                                                u32::from(row) * u32::from(dy) + u32::from(y)
                                            )
                                            .is_some(),
                                            "{renderer:?} shape{shape} height{height}: tiny text escaped head authority"
                                        );
                                    }
                                }
                                marks.push((col, cell.symbol().to_owned()));
                            }
                        }
                    }
                    marks.sort_by_key(|p| p.0);
                    let literal: String = marks.iter().map(|p| p.1.as_str()).collect();
                    if !literal.is_empty() {
                        assert!(
                            literal == canonical || literal == core,
                            "{renderer:?} shape{shape} height{height}: incomplete tiny expression {literal}"
                        );
                        readable_two_row_cases += usize::from(height == 2);
                    }
                    assert!(!text(&buf, area).contains("curious"));
                    assert_eq!(buf[(1, 3)].symbol(), " ");
                    assert_eq!(buf[(12, 3)].symbol(), " ");
                }
            }
        }
        assert!(
            readable_two_row_cases > 0,
            "real two-row head surfaces must exercise readable native cores"
        );
    }

    #[test]
    fn mapped_micro_occluders_never_receive_a_literal_face_and_legacy_tiny_text_still_works() {
        let config = Config::sample().unwrap();
        let colors = Colors::from_config(&config, Depth::Indexed).unwrap();
        let sprite = Sprite::voxel_with_appearance(
            Stage::Child,
            Expression::Sick,
            SpriteStyle::SixteenBit,
            1,
            0,
            false,
            Appearance {
                shape: 14,
                body: 9,
                accent: 6,
            },
        );
        let bounds = head_surface_bounds(&sprite).unwrap();
        let (nose_x, nose_y) = (bounds.top as u32..bounds.bottom as u32)
            .flat_map(|y| (bounds.left as u32..bounds.right as u32).map(move |x| (x, y)))
            .find(|&(x, y)| {
                sprite.pixel(x, y, 1) == Some(7) && valid_head_uv(&sprite, x, y).is_none()
            })
            .expect("actual bird beak must provide a positive opaque occlusion fixture");
        for renderer in [
            Renderer::Ascii,
            Renderer::HalfBlock,
            Renderer::Quadrant,
            Renderer::Braille,
        ] {
            let area = Rect::new(0, 0, 10, 2);
            let (dx, dy) = renderer.packing();
            let scale = 100.0 * f64::from(dx);
            let projection = Projection {
                left: 5.0 * f64::from(dx) - (f64::from(nose_x) + 0.5) * scale,
                top: f64::from(dy)
                    - (f64::from(nose_y) + 0.5) * scale * camera_pixel_aspect(renderer),
                scale,
                scale_y: scale * camera_pixel_aspect(renderer),
            };
            let mut before = Buffer::empty(area);
            draw_projection(&sprite, renderer, &colors, projection, area, &mut before);
            let mut after = before.clone();
            assert!(!draw_camera_face(
                &sprite, renderer, &colors, projection, area, &mut after
            ));
            assert_eq!(
                after, before,
                "{renderer:?}: a tiny beak crop must remain unpainted"
            );
        }
        let legacy = Sprite::animated(Stage::Adult, Expression::Happy, SpriteStyle::EightBit, 0);
        assert!(legacy.face_text().is_none());
        let area = Rect::new(0, 0, 10, 2);
        let projection = Projection::camera(
            &legacy,
            SolidBounds::of(&legacy).unwrap(),
            area,
            Renderer::Ascii,
            0,
            (500, 500),
            Camera {
                orbit_degrees: 45,
                view: CameraView::Face,
                ..Camera::default()
            },
        );
        let mut buf = Buffer::empty(area);
        assert!(draw_camera_face(
            &legacy,
            Renderer::Ascii,
            &colors,
            projection,
            area,
            &mut buf
        ));
        assert!(text(&buf, area).contains(Expression::Happy.kaomoji(0)));
    }

    #[test]
    fn optional_parentheses_cannot_starve_a_three_cell_native_expression_core() {
        let texture = FaceTexture {
            text: "(^_^)",
            aspect: None,
        };
        let visible = [
            ((1, 1), [0.20, 0.35], 0.22),
            ((2, 1), [0.50, 0.35], 0.22),
            ((3, 1), [0.70, 0.35], 0.22),
        ];
        let candidates = native_glyph_candidates(texture, 0.35, &visible);
        assert_eq!(candidates[1], Some(((1, 1), b'^')));
        assert_eq!(candidates[2], Some(((2, 1), b'_')));
        assert_eq!(candidates[3], Some(((3, 1), b'^')));
        assert!(candidates[0].is_none() && candidates[4].is_none());
        let missing_mouth = [visible[0], visible[2]];
        let incomplete = native_glyph_candidates(texture, 0.35, &missing_mouth);
        assert!(
            incomplete[1..4].iter().any(Option::is_none),
            "two eye cells cannot masquerade as a complete expression core"
        );
    }

    #[test]
    fn micro_text_runs_are_adjacent_and_cannot_bridge_occluders_or_reverse_head_uvs() {
        let texture = FaceTexture {
            text: "(^_^)",
            aspect: None,
        };
        let visible: Vec<NativeHeadCell> = (0..5)
            .map(|index| {
                (
                    (index, 1),
                    [(f64::from(index) * 8.0 + 3.5) / 39.0, 0.35],
                    8.0 / 39.0,
                )
            })
            .collect();
        let complete = native_micro_candidates(texture, &visible);
        assert_eq!(
            complete
                .iter()
                .flatten()
                .map(|(_, b)| char::from(*b))
                .collect::<String>(),
            "(^_^)"
        );
        let core = native_micro_candidates(texture, &visible[1..4]);
        assert!(core[0].is_none() && core[4].is_none());
        assert_eq!(
            core.iter()
                .flatten()
                .map(|(_, b)| char::from(*b))
                .collect::<String>(),
            "^_^"
        );
        let interrupted: Vec<_> = visible
            .iter()
            .copied()
            .filter(|(pos, _, _)| pos.0 != 2)
            .collect();
        assert!(
            native_micro_candidates(texture, &interrupted)
                .iter()
                .all(Option::is_none),
            "an unmapped nose cell must interrupt the whole text run"
        );
        let mut reversed = visible.clone();
        for (index, (_, uv, _)) in reversed.iter_mut().enumerate() {
            uv[0] = visible[4 - index].1[0];
        }
        assert!(
            native_micro_candidates(texture, &reversed)
                .iter()
                .all(Option::is_none),
            "a mirrored UV order must not become a forward-facing expression"
        );
    }

    #[test]
    fn tiny_bird_heads_keep_complete_native_or_separate_inline_expression_without_faking_egg_faces()
    {
        let colors = Colors::from_config(&Config::sample().unwrap(), Depth::Mono).unwrap();
        let mut inline_cases = 0;
        for style in [SpriteStyle::EightBit, SpriteStyle::SixteenBit] {
            for frame in 0..8 {
                let sprite = Sprite::voxel_with_appearance(
                    Stage::Child,
                    Expression::Sick,
                    style,
                    frame,
                    0,
                    false,
                    Appearance {
                        shape: 14,
                        body: 9,
                        accent: 12,
                    },
                );
                let canonical = sprite.face_text().unwrap();
                let core = &canonical[1..canonical.len() - 1];
                for renderer in [
                    Renderer::Ascii,
                    Renderer::HalfBlock,
                    Renderer::Quadrant,
                    Renderer::Braille,
                ] {
                    let area = Rect::new(2, 2, 10, 2);
                    let mut buf = Buffer::empty(Rect::new(0, 0, 14, 6));
                    RoomWidget {
                        world: None,
                        detail: style,
                        sprite: &sprite,
                        renderer,
                        scale: 0,
                        colors: &colors,
                        expression: Expression::Sick,
                        frame,
                        position: (500, 500),
                        camera: Camera {
                            orbit_degrees: 45,
                            view: CameraView::Face,
                            ..Camera::default()
                        },
                        habitat: None,
                    }
                    .render(area, &mut buf);
                    let rendered = text(&buf, area);
                    assert!(
                        rendered.contains(canonical) || rendered.contains(core),
                        "{style:?} {renderer:?} frame{frame}: complete eye/mouth core missing: {rendered}"
                    );
                    if !buf
                        .content
                        .iter()
                        .any(|cell| cell.modifier.contains(ratatui::style::Modifier::BOLD))
                    {
                        let row = Rect::new(area.x, area.bottom() - 1, area.width, 1);
                        let caption = text(&buf, row);
                        assert!(caption.trim() == canonical || caption.trim() == core);
                        assert!((area.x..area.right()).all(|x| buf[(x, row.y)].bg == colors.ink(6)));
                        inline_cases += 1;
                    }
                    assert_eq!(buf[(1, 2)].symbol(), " ");
                    assert_eq!(buf[(12, 2)].symbol(), " ");
                }
            }
            let egg = Sprite::voxel(Stage::Egg, Expression::Idle, style, 0, 0, false);
            assert!(egg.face_text().is_none());
            let area = Rect::new(0, 0, 10, 2);
            let mut buf = Buffer::empty(area);
            RoomWidget {
                world: None,
                detail: style,
                sprite: &egg,
                renderer: Renderer::Ascii,
                scale: 0,
                colors: &colors,
                expression: Expression::Idle,
                frame: 0,
                position: (500, 500),
                camera: Camera {
                    orbit_degrees: 45,
                    view: CameraView::Face,
                    ..Camera::default()
                },
                habitat: None,
            }
            .render(area, &mut buf);
            assert!(
                !text(&buf, area).contains("^_^"),
                "closed eggs must not acquire a fallback face"
            );
        }
        assert!(
            inline_cases > 0,
            "the real tiny beak fixture must exercise the separate inline row"
        );
    }

    fn facial_marks(sprite: &Sprite, renderer: Renderer, head_columns: u16) -> (Buffer, Buffer) {
        let config = Config::sample().unwrap();
        let colors = Colors::from_config(&config, Depth::Indexed).unwrap();
        let area = Rect::new(0, 0, 160, 80);
        let face = sprite.face_overlay().unwrap();
        let (left, right) = head_span(sprite, face.center_x, face.y);
        let (dx, dy) = renderer.packing();
        let scale = f64::from(head_columns) * f64::from(dx) / (right - left);
        let projection = Projection {
            left: 80.0 * f64::from(dx) - (f64::from(face.center_x) + 0.5) * scale,
            top: 40.0 * f64::from(dy)
                - (f64::from(face.y) + 0.5) * scale * camera_pixel_aspect(renderer),
            scale,
            scale_y: scale * camera_pixel_aspect(renderer),
        };
        let mut before = Buffer::empty(area);
        draw_projection(sprite, renderer, &colors, projection, area, &mut before);
        let mut after = before.clone();
        assert!(draw_camera_face(
            sprite, renderer, &colors, projection, area, &mut after
        ));
        (before, after)
    }

    fn mark_bounds(before: &Buffer, after: &Buffer) -> (u16, u16, usize) {
        let mut points = Vec::new();
        for y in before.area.y..before.area.bottom() {
            for x in before.area.x..before.area.right() {
                if before[(x, y)] != after[(x, y)] {
                    points.push((x, y));
                }
            }
        }
        assert!(!points.is_empty());
        let width = points.iter().map(|p| p.0).max().unwrap()
            - points.iter().map(|p| p.0).min().unwrap()
            + 1;
        let height = points.iter().map(|p| p.1).max().unwrap()
            - points.iter().map(|p| p.1).min().unwrap()
            + 1;
        (width, height, points.len())
    }

    #[test]
    fn thin_atlas_keeps_curved_parentheses_eyes_and_mouth_separate() {
        let texture = FaceTexture {
            text: "(^_^)",
            aspect: None,
        };
        let atlas: Vec<String> = (0..9)
            .map(|y| {
                (0..37)
                    .map(|x| if texture.bit(x, y) { '#' } else { ' ' })
                    .collect()
            })
            .collect();
        assert_eq!(atlas[0], "   #                             #   ");
        // The same outer stem persists through three rows: a curve, not < or >.
        assert!(atlas[3..6]
            .iter()
            .all(|row| row.starts_with('#') && row.ends_with('#')));
        for glyph in 0..4 {
            for x in glyph * 8 + 5..glyph * 8 + 8 {
                for y in 0..9 {
                    let uv = [(f64::from(x) + 1.5) / 39.0, (f64::from(y) + 1.5) / 11.0];
                    assert!(
                        !texture.sample(uv, [1.0 / 39.0, 1.0 / 11.0]),
                        "ink bridged the side bearing at {x},{y}"
                    );
                }
            }
        }
        assert!(texture.bit(10, 1) && texture.bit(26, 1)); // two caret peaks
        assert!((16..21).all(|x| texture.bit(x, 8))); // separate mouth baseline
        assert!((16..21).all(|x| (0..8).all(|y| !texture.bit(x, y))));
        assert_ne!(face_glyph(b'('), face_glyph(b'<'));
        assert_ne!(face_glyph(b')'), face_glyph(b'>'));
    }

    #[test]
    fn expanded_expression_glyphs_keep_w_valleys_three_counters_and_q_tail() {
        let w = face_glyph(b'w').unwrap();
        let three = face_glyph(b'3').unwrap();
        let q = face_glyph(b'Q').unwrap();
        assert_eq!(w[6], 0b01010, "w needs two distinct lower valleys");
        assert_ne!(w, face_glyph(b'u').unwrap());
        assert_eq!(three[3], 0b00010, "3 keeps its upper counter open");
        assert_eq!(three[5], 0b00001, "3 keeps its lower counter open");
        assert_eq!(q[7], 0b00001, "Q needs a descending tail");
        assert_ne!(q, face_glyph(b'O').unwrap());
        for text in ["(^w^)", "(o3o)", "(Q_Q)"] {
            let texture = FaceTexture { text, aspect: None };
            for (index, glyph) in text.bytes().enumerate() {
                let rows =
                    face_glyph(glyph).expect("every new expression character needs a font glyph");
                for (y, row) in rows.into_iter().enumerate() {
                    for x in 0..5 {
                        let uv = [
                            (index as f64 * 8.0 + f64::from(x) + 1.5) / 39.0,
                            (y as f64 + 1.5) / 11.0,
                        ];
                        assert_eq!(
                            texture.sample(uv, [0.0; 2]),
                            row & (1 << (4 - x)) != 0,
                            "{text} character{index} pixel{x},{y}"
                        );
                    }
                }
            }
        }
    }

    #[test]
    fn coverage_rejects_a_grazed_stroke_instead_of_dilating_it() {
        let texture = FaceTexture {
            text: "(^_^)",
            aspect: None,
        };
        let uv = |x: f64, y: f64| [(x + 1.0) / 39.0, (y + 1.0) / 11.0];
        assert!(texture.sample(uv(10.5, 1.5), [0.0, 0.0]));
        // The footprint touches the caret, but 90% of the sample is clear.
        assert!(!texture.sample(uv(11.45, 1.5), [1.0 / 39.0, 0.5 / 11.0]));
        for footprint in [[0.0; 2], [1.0 / 39.0, 1.0 / 11.0], [1.0; 2]] {
            assert!(!texture.sample(uv(14.5, 4.5), footprint));
            assert!(!texture.sample(uv(22.5, 4.5), footprint));
        }
    }

    #[test]
    fn phone_glyph_lod_is_legible_and_every_changed_cell_is_visible_head_material() {
        let colors = Colors::from_config(&Config::sample().unwrap(), Depth::Indexed).unwrap();
        for yaw in [0, 45, 180, 315] {
            let sprite = Sprite::voxel(
                Stage::Adult,
                Expression::Happy,
                SpriteStyle::SixteenBit,
                0,
                yaw,
                false,
            );
            for renderer in [
                Renderer::Ascii,
                Renderer::HalfBlock,
                Renderer::Quadrant,
                Renderer::Braille,
            ] {
                let area = Rect::new(2, 2, 62, 30);
                let bounds = SolidBounds::of(&sprite).unwrap();
                let projection = Projection::camera(
                    &sprite,
                    bounds,
                    area,
                    renderer,
                    0,
                    (500, 500),
                    Camera {
                        orbit_degrees: 45,
                        view: CameraView::Face,
                        ..Camera::default()
                    },
                );
                let mut before = Buffer::empty(Rect::new(0, 0, 66, 34));
                draw_projection(&sprite, renderer, &colors, projection, area, &mut before);
                let mut after = before.clone();
                let painted =
                    draw_surface_glyphs(&sprite, renderer, &colors, projection, area, &mut after);
                let mut glyphs = Vec::new();
                let (dx, dy) = renderer.packing();
                for row in after.area.y..after.area.bottom() {
                    for col in after.area.x..after.area.right() {
                        assert_eq!(before[(col, row)].bg, after[(col, row)].bg);
                        if before[(col, row)] != after[(col, row)] {
                            assert!(area.contains((col, row).into()));
                            glyphs.push(after[(col, row)].symbol().to_owned());
                            for y in 0..dy {
                                for x in 0..dx {
                                    assert!(
                                        projected_head_uv(
                                            &sprite,
                                            projection,
                                            u32::from(col) * u32::from(dx) + u32::from(x),
                                            u32::from(row) * u32::from(dy) + u32::from(y)
                                        )
                                        .is_some(),
                                        "{renderer:?} yaw {yaw}: literal covered a nose/occluder"
                                    );
                                }
                            }
                        }
                    }
                }
                if yaw == 0 {
                    assert!(painted);
                    assert_eq!(glyphs.len(), 5, "{renderer:?}: {glyphs:?}");
                    assert_eq!(glyphs.iter().filter(|s| s.as_str() == "^").count(), 2);
                    assert!(glyphs.iter().any(|s| s == "(") && glyphs.iter().any(|s| s == ")"));
                    assert!(glyphs.iter().any(|s| s == "_"));
                } else if yaw == 180 {
                    assert!(!painted && glyphs.is_empty());
                }
            }
        }
    }

    #[test]
    fn five_to_seven_cell_faces_choose_complete_native_cores_and_balanced_parentheses() {
        let sprite = Sprite::voxel(
            Stage::Adult,
            Expression::Happy,
            SpriteStyle::SixteenBit,
            0,
            0,
            false,
        );
        let colors = Colors::from_config(&Config::sample().unwrap(), Depth::Indexed).unwrap();
        let bounds = head_surface_bounds(&sprite).unwrap();
        let area = Rect::new(0, 0, 64, 30);
        let mut core_cases = 0;
        for renderer in [
            Renderer::Ascii,
            Renderer::HalfBlock,
            Renderer::Quadrant,
            Renderer::Braille,
        ] {
            let (dx, dy) = renderer.packing();
            for columns in [5.25, 6.25, 7.25] {
                let scale = columns * f64::from(dx) / bounds.width();
                let projection = Projection {
                    left: 32.0 * f64::from(dx) - (bounds.left + bounds.right) / 2.0 * scale,
                    top: 15.0 * f64::from(dy)
                        - (bounds.top + bounds.bottom) / 2.0
                            * scale
                            * camera_pixel_aspect(renderer),
                    scale,
                    scale_y: scale * camera_pixel_aspect(renderer),
                };
                let mut before = Buffer::empty(area);
                draw_projection(&sprite, renderer, &colors, projection, area, &mut before);
                let mut expected = before.clone();
                draw_surface_glyphs(&sprite, renderer, &colors, projection, area, &mut expected);
                let mut actual = before.clone();
                draw_camera_face(&sprite, renderer, &colors, projection, area, &mut actual);
                assert_eq!(
                    actual, expected,
                    "{renderer:?} {columns} cells must enter native text, never the bitmap atlas"
                );
                let mut marks: Vec<_> = before
                    .content
                    .iter()
                    .zip(&actual.content)
                    .enumerate()
                    .filter(|(_, (a, b))| a != b)
                    .map(|(index, (_, b))| (index % usize::from(area.width), b.symbol()))
                    .collect();
                marks.sort_by_key(|p| p.0);
                let literal: String = marks.iter().map(|p| p.1).collect();
                if !literal.is_empty() {
                    assert!(
                        matches!(literal.as_str(), "^_^" | "(^_^)"),
                        "{renderer:?} {columns} cells: missing eye/mouth or unbalanced parentheses: {literal}"
                    );
                    core_cases += 1;
                }
            }
            let scale = 4.0 * f64::from(dx) / bounds.width();
            let projection = Projection {
                left: 32.0 * f64::from(dx) - (bounds.left + bounds.right) / 2.0 * scale,
                top: 15.0 * f64::from(dy)
                    - (bounds.top + bounds.bottom) / 2.0 * scale * camera_pixel_aspect(renderer),
                scale,
                scale_y: scale * camera_pixel_aspect(renderer),
            };
            let mut before = Buffer::empty(area);
            draw_projection(&sprite, renderer, &colors, projection, area, &mut before);
            let mut after = before.clone();
            assert!(!draw_camera_face(
                &sprite, renderer, &colors, projection, area, &mut after
            ));
            assert_eq!(
                after, before,
                "a four-cell surface must not pretend to resolve five glyphs"
            );
        }
        assert!(
            core_cases > 0,
            "small native text must exercise complete two-eye/mouth cores"
        );
    }

    #[test]
    fn phone_room_and_pet_at_fifty_percent_zoom_show_native_eyes_and_mouth() {
        let sprite = Sprite::voxel(
            Stage::Adult,
            Expression::Happy,
            SpriteStyle::SixteenBit,
            0,
            0,
            false,
        );
        let colors = Colors::from_config(&Config::sample().unwrap(), Depth::Indexed).unwrap();
        for (width, height) in [(62, 30), (61, 26)] {
            let area = Rect::new(0, 0, width, height);
            for view in [CameraView::Pet, CameraView::Room] {
                for renderer in [
                    Renderer::Ascii,
                    Renderer::HalfBlock,
                    Renderer::Quadrant,
                    Renderer::Braille,
                ] {
                    let projection = Projection::camera(
                        &sprite,
                        SolidBounds::of(&sprite).unwrap(),
                        area,
                        renderer,
                        0,
                        (500, 500),
                        Camera {
                            orbit_degrees: 45,
                            view,
                            from_view: view,
                            zoom_percent: 50,
                            transition_percent: 100,
                        },
                    );
                    let mut before = Buffer::empty(area);
                    draw_projection(&sprite, renderer, &colors, projection, area, &mut before);
                    let mut after = before.clone();
                    assert!(draw_camera_face(
                        &sprite, renderer, &colors, projection, area, &mut after
                    ));
                    let mut chars = Vec::new();
                    for y in 0..height {
                        for x in 0..width {
                            assert_eq!(before[(x, y)].bg, after[(x, y)].bg);
                            if before[(x, y)] != after[(x, y)] {
                                chars.push((x, after[(x, y)].symbol().to_owned()));
                            }
                        }
                    }
                    chars.sort_by_key(|p| p.0);
                    let literal: String = chars.iter().map(|p| p.1.as_str()).collect();
                    assert!(
                        matches!(literal.as_str(), "^_^" | "(^_^)"),
                        "{width}x{height} {renderer:?} {view:?} zoom50 needs native eyes/mouth with balanced optional parentheses: {literal}"
                    );
                }
            }
        }
    }

    #[test]
    fn bird_child_sick_frames_keep_native_eyes_and_mouth_at_small_head_scales() {
        for (style, frame) in [SpriteStyle::EightBit, SpriteStyle::SixteenBit]
            .into_iter()
            .flat_map(|style| {
                (0..Expression::Sick.kaomoji_cycle().len()).map(move |frame| (style, frame))
            })
        {
            let sprite = Sprite::voxel_with_appearance(
                Stage::Child,
                Expression::Sick,
                style,
                frame,
                0,
                false,
                Appearance {
                    shape: 14,
                    body: 9,
                    accent: 6,
                },
            );
            let canonical = Expression::Sick.kaomoji(frame);
            let core = &canonical[1..4];
            for renderer in [
                Renderer::Ascii,
                Renderer::HalfBlock,
                Renderer::Quadrant,
                Renderer::Braille,
            ] {
                for columns in [8, 12, 16] {
                    let (before, after) = facial_marks(&sprite, renderer, columns);
                    let mut marks = Vec::new();
                    for y in after.area.y..after.area.bottom() {
                        for x in after.area.x..after.area.right() {
                            assert_eq!(before[(x, y)].bg, after[(x, y)].bg);
                            if before[(x, y)] != after[(x, y)] {
                                marks.push((x, after[(x, y)].symbol().to_owned()));
                            }
                        }
                    }
                    marks.sort_by_key(|p| p.0);
                    let literal: String = marks.iter().map(|p| p.1.as_str()).collect();
                    assert!(
                        literal == canonical || literal == core,
                        "actual bird child Sick {style:?} frame{frame} {renderer:?} head{columns}: missing native eye/mouth or lone bracket: {literal}"
                    );
                }
            }
        }
    }

    #[test]
    fn bird_child_sick_phone_camera_zoom_keeps_complete_literal_expression_cores() {
        let colors = Colors::from_config(&Config::sample().unwrap(), Depth::Indexed).unwrap();
        for (style, frame) in [SpriteStyle::EightBit, SpriteStyle::SixteenBit]
            .into_iter()
            .flat_map(|style| {
                (0..Expression::Sick.kaomoji_cycle().len()).map(move |frame| (style, frame))
            })
        {
            let sprite = Sprite::voxel_with_appearance(
                Stage::Child,
                Expression::Sick,
                style,
                frame,
                0,
                false,
                Appearance {
                    shape: 14,
                    body: 9,
                    accent: 6,
                },
            );
            let canonical = Expression::Sick.kaomoji(frame);
            let core = &canonical[1..4];
            for width in [54, 62] {
                let area = Rect::new(0, 0, width, 18);
                for view in [CameraView::Pet, CameraView::Room] {
                    for zoom in [50, 75] {
                        for renderer in [Renderer::Ascii, Renderer::HalfBlock] {
                            let projection = Projection::camera(
                                &sprite,
                                SolidBounds::of(&sprite).unwrap(),
                                area,
                                renderer,
                                0,
                                (500, 500),
                                Camera {
                                    orbit_degrees: 45,
                                    view,
                                    from_view: view,
                                    zoom_percent: zoom,
                                    transition_percent: 100,
                                },
                            );
                            let mut before = Buffer::empty(area);
                            draw_projection(
                                &sprite,
                                renderer,
                                &colors,
                                projection,
                                area,
                                &mut before,
                            );
                            let mut after = before.clone();
                            assert!(
                                draw_camera_face(
                                    &sprite, renderer, &colors, projection, area, &mut after
                                ),
                                "bird child Sick {style:?} frame{frame} {renderer:?} {view:?} zoom{zoom} width{width}: no complete native core"
                            );
                            let mut marks = Vec::new();
                            for y in 0..area.height {
                                for x in 0..area.width {
                                    assert_eq!(before[(x, y)].bg, after[(x, y)].bg);
                                    if before[(x, y)] != after[(x, y)] {
                                        marks.push((x, after[(x, y)].symbol().to_owned()));
                                    }
                                }
                            }
                            marks.sort_by_key(|p| p.0);
                            let literal: String = marks.iter().map(|p| p.1.as_str()).collect();
                            assert!(
                                literal == canonical || literal == core,
                                "bird child Sick {style:?} frame{frame} {renderer:?} {view:?} zoom{zoom} width{width}: {literal}"
                            );
                        }
                    }
                }
            }
        }
    }

    #[test]
    fn camera_glyph_strokes_grow_with_the_head_in_every_packing() {
        let sprite = Sprite::voxel(
            Stage::Adult,
            Expression::Happy,
            SpriteStyle::SixteenBit,
            0,
            0,
            false,
        );
        for renderer in [
            Renderer::Ascii,
            Renderer::HalfBlock,
            Renderer::Quadrant,
            Renderer::Braille,
        ] {
            for columns in [12, 16] {
                let (small_before, small_after) = facial_marks(&sprite, renderer, columns);
                // At this resolution the meaningful result is separated,
                // readable terminal glyphs, not a minimum area of bitmap ink.
                // In particular a one-row kaomoji must not fail for being thin.
                let mut visible = Vec::new();
                for y in small_after.area.y..small_after.area.bottom() {
                    for x in small_after.area.x..small_after.area.right() {
                        let before = &small_before[(x, y)];
                        let after = &small_after[(x, y)];
                        assert_eq!(before.bg, after.bg, "{renderer:?}: Pet {columns}");
                        if before != after {
                            let glyph = after.symbol().chars().next().unwrap();
                            assert!("(^_^)".contains(glyph), "{renderer:?}: {glyph}");
                            visible.push((x, y, glyph));
                        }
                    }
                }
                let mut eyes: Vec<_> = visible.iter().filter(|p| p.2 == '^').collect();
                eyes.sort_by_key(|p| p.0);
                assert_eq!(eyes.len(), 2, "{renderer:?}: Pet {columns}: {visible:?}");
                let left = visible
                    .iter()
                    .find(|p| p.2 == '(')
                    .expect("small head must retain a clean opening parenthesis");
                let right = visible
                    .iter()
                    .find(|p| p.2 == ')')
                    .expect("small head must retain a clean closing parenthesis");
                assert!(
                    left.0 < eyes[0].0 && eyes[0].0 + 1 < eyes[1].0 && eyes[1].0 < right.0,
                    "{renderer:?}: Pet {columns}: merged or reordered features {visible:?}"
                );
                let mouth = visible.iter().find(|p| p.2 == '_').expect(
                    "frontal small head must retain its literal mouth in the upper face band",
                );
                assert_eq!(visible.len(), 5, "{renderer:?}: Pet {columns}: {visible:?}");
                assert!(
                    eyes[0].0 < mouth.0 && mouth.0 < eyes[1].0,
                    "{renderer:?}: Pet {columns}: mouth must sit between the eyes {visible:?}"
                );
                let top = visible.iter().map(|p| p.1).min().unwrap();
                let bottom = visible.iter().map(|p| p.1).max().unwrap();
                assert!(
                    bottom - top <= 1,
                    "{renderer:?}: Pet {columns}: canonical native text broke across bands {visible:?}"
                );
            }
            let marks: Vec<_> = [28, 44, 68]
                .into_iter()
                .map(|columns| {
                    let (before, after) = facial_marks(&sprite, renderer, columns);
                    mark_bounds(&before, &after)
                })
                .collect();
            assert!(
                marks.windows(2).all(|m| m[1].0 > m[0].0 && m[1].2 > m[0].2),
                "{renderer:?}: {marks:?}"
            );
            // A 44-column head must contain actual multi-row facial strokes,
            // rather than a five-cell label inside an otherwise enlarged head.
            assert!(
                marks[1].0 >= 30 && marks[1].1 >= 3,
                "{renderer:?}: {marks:?}"
            );
        }
    }

    #[test]
    fn surface_textures_follow_expression_changes_and_keep_full_text_on_oblique_heads() {
        for expression in [Expression::Idle, Expression::Eating] {
            let make = |frame| {
                Sprite::voxel(
                    Stage::Adult,
                    expression,
                    SpriteStyle::SixteenBit,
                    frame,
                    0,
                    false,
                )
            };
            let first = make(0);
            let next = make(if expression == Expression::Idle { 2 } else { 1 });
            assert_ne!(
                first.face_overlay().unwrap().text,
                next.face_overlay().unwrap().text
            );
            for renderer in [
                Renderer::Ascii,
                Renderer::HalfBlock,
                Renderer::Quadrant,
                Renderer::Braille,
            ] {
                let (_, first) = facial_marks(&first, renderer, 44);
                let (_, next) = facial_marks(&next, renderer, 44);
                assert_ne!(first, next, "{renderer:?} {expression:?}");
            }
        }
        for yaw in [45, 315] {
            let sprite = Sprite::voxel(
                Stage::Adult,
                Expression::Happy,
                SpriteStyle::SixteenBit,
                0,
                yaw,
                false,
            );
            let metadata = sprite.face_overlay().expect("authorized near-side span");
            assert_eq!(metadata.text.len(), 3);
            let texture = FaceTexture::new(&sprite).unwrap();
            assert_eq!(texture.text, sprite.face_text().unwrap());
            assert_eq!(texture.text.len(), 5); // Actual surface occlusion clips full texture.
            assert_ne!(texture.text, metadata.text);
            assert!(FaceTexture::bounds(&sprite).is_some());
            for renderer in [Renderer::HalfBlock, Renderer::Quadrant, Renderer::Braille] {
                let (before, after) = facial_marks(&sprite, renderer, 44);
                assert!(mark_bounds(&before, &after).0 > 5);
            }
        }
    }

    #[test]
    fn surface_textures_preserve_rear_silhouettes_clipping_and_literal_snapshot_fallback() {
        let config = Config::sample().unwrap();
        let colors = Colors::from_config(&config, Depth::Indexed).unwrap();
        let front = Sprite::voxel(
            Stage::Adult,
            Expression::Happy,
            SpriteStyle::SixteenBit,
            0,
            0,
            false,
        );
        let rear = Sprite::voxel(
            Stage::Adult,
            Expression::Happy,
            SpriteStyle::SixteenBit,
            0,
            180,
            false,
        );
        for renderer in [
            Renderer::Ascii,
            Renderer::HalfBlock,
            Renderer::Quadrant,
            Renderer::Braille,
        ] {
            let area = Rect::new(4, 5, 12, 6);
            let mut buf = Buffer::empty(Rect::new(0, 0, 30, 20));
            for cell in &mut buf.content {
                cell.set_char('!');
            }
            let before = buf.clone();
            let face = front.face_overlay().unwrap();
            let (dx, dy) = renderer.packing();
            let projection = Projection {
                left: 10.0 * f64::from(dx) - f64::from(face.center_x) * 8.0,
                top: 8.0 * f64::from(dy) - f64::from(face.y) * 8.0 * camera_pixel_aspect(renderer),
                scale: 8.0,
                scale_y: 8.0 * camera_pixel_aspect(renderer),
            };
            draw_camera_face(&front, renderer, &colors, projection, area, &mut buf);
            for y in 0..20 {
                for x in 0..30 {
                    if !area.contains((x, y).into()) {
                        assert_eq!(buf[(x, y)], before[(x, y)]);
                    }
                }
            }
            let before_rear = buf.clone();
            assert!(!draw_camera_face(
                &rear, renderer, &colors, projection, area, &mut buf
            ));
            assert_eq!(before_rear, buf);
        }
        let text = snapshot(&front, &config).unwrap();
        assert!(text.is_ascii());
        assert!(text.contains(front.face_overlay().unwrap().text));
        for expression in [
            Expression::Idle,
            Expression::Happy,
            Expression::Sad,
            Expression::Angry,
            Expression::Surprised,
            Expression::Sleeping,
            Expression::Sick,
            Expression::Hungry,
            Expression::Playful,
            Expression::Shy,
            Expression::Excited,
            Expression::Eating,
            Expression::Cleaning,
            Expression::Healing,
            Expression::Celebrate,
            Expression::Dead,
        ] {
            for text in expression.kaomoji_cycle() {
                assert!(
                    text.bytes().all(|b| face_glyph(b).is_some()),
                    "{expression:?} expression has an unsupported atlas character: {text}"
                );
            }
        }
    }

    #[test]
    fn portrait_and_landscape_resize_keep_zoomed_faces_bounded_and_reproducible() {
        let config = Config::sample().unwrap();
        let colors = Colors::from_config(&config, Depth::Indexed).unwrap();
        let sprite = Sprite::voxel(
            Stage::Adult,
            Expression::Happy,
            SpriteStyle::SixteenBit,
            1,
            0,
            false,
        );
        let original = snapshot(&sprite, &config).unwrap();
        let buffer_area = Rect::new(0, 0, 110, 60);
        for renderer in [
            Renderer::Ascii,
            Renderer::HalfBlock,
            Renderer::Quadrant,
            Renderer::Braille,
        ] {
            for view in [CameraView::Pet, CameraView::Face] {
                for zoom_percent in [50, 100, 200, 400] {
                    let mut first = None;
                    for (width, height) in [(100, 24), (30, 40), (40, 50), (20, 8), (100, 24)] {
                        let area = Rect::new(3, 4, width, height);
                        let mut buf = Buffer::empty(buffer_area);
                        for cell in &mut buf.content {
                            cell.set_char('!');
                        }
                        let before = buf.clone();
                        RoomWidget {
                            world: None,
                            detail: SpriteStyle::SixteenBit,
                            sprite: &sprite,
                            renderer,
                            scale: 0,
                            colors: &colors,
                            expression: Expression::Happy,
                            frame: 1,
                            position: (650, 450),
                            camera: Camera {
                                view,
                                zoom_percent,
                                ..Camera::default()
                            },
                            habitat: None,
                        }
                        .render(area, &mut buf);
                        for y in buffer_area.y..buffer_area.bottom() {
                            for x in buffer_area.x..buffer_area.right() {
                                if !area.contains((x, y).into()) {
                                    assert_eq!(buf[(x, y)], before[(x, y)]);
                                }
                            }
                        }
                        if (width, height) == (100, 24) {
                            if let Some(first) = &first {
                                assert_eq!(first, &buf, "{renderer:?}/{view:?}/{zoom_percent}");
                            } else {
                                first = Some(buf);
                            }
                        }
                    }
                }
            }
        }
        assert_eq!(original, snapshot(&sprite, &config).unwrap());
    }

    #[test]
    fn surface_texture_preserves_species_material_through_stroke_gaps() {
        // This point is a transparent gap above the mouth. Underlying species
        // material must survive; a universal cream plate would erase its mask.
        let texture = FaceTexture {
            text: "(^_^)",
            aspect: None,
        };
        assert!(!texture.sample([10.25 / 21.0, 2.25 / 7.0], [0.0, 0.0]));

        let config = Config::sample().unwrap();
        let sprite = Sprite::voxel(
            Stage::Adult,
            Expression::Happy,
            SpriteStyle::SixteenBit,
            0,
            0,
            false,
        );
        let metadata = sprite.face_overlay().unwrap();
        for depth in [Depth::Indexed, Depth::Mono] {
            let colors = Colors::from_config(&config, depth).unwrap();
            for renderer in [
                Renderer::Ascii,
                Renderer::HalfBlock,
                Renderer::Quadrant,
                Renderer::Braille,
            ] {
                let (dx, dy) = renderer.packing();
                let area = Rect::new(0, 0, 160, 80);
                let projection = Projection {
                    left: 80.0 * f64::from(dx) - (f64::from(metadata.center_x) + 0.5) * 6.0,
                    top: 40.0 * f64::from(dy)
                        - (f64::from(metadata.y) + 0.5) * 6.0 * camera_pixel_aspect(renderer),
                    scale: 6.0,
                    scale_y: 6.0 * camera_pixel_aspect(renderer),
                };
                let texture = FaceTexture::new(&sprite).unwrap();
                let face = FaceTexture::bounds(&sprite).unwrap();
                let (left, top) = projection.cell(face.left, face.top, renderer);
                let (right, bottom) = projection.cell(face.right, face.bottom, renderer);
                // One-plane ASCII shades at this size: its gaps are the cells the
                // shading leaves without ink, and they must equal the body too.
                let shaded = face_lod(texture, right - left, bottom - top, (dx, dy) != (1, 1))
                    == FaceLod::Shaded;
                let mut source = Buffer::empty(area);
                draw_projection(&sprite, renderer, &colors, projection, area, &mut source);
                let mut buf = source.clone();
                for cell in &mut buf.content {
                    // Plant stale foreground marks while retaining the actual
                    // room plane, so restoring a gap cannot invent a backdrop.
                    cell.set_char('X').set_fg(colors.ink(5));
                }
                let before = buf.clone();
                assert!(draw_camera_face(
                    &sprite, renderer, &colors, projection, area, &mut buf
                ));
                let mut cleared_gaps = 0;
                let mut noncream_gaps = 0;
                let mut untouched = 0;
                for row in area.y..area.bottom() {
                    for col in area.x..area.right() {
                        let mut clear_gap = true;
                        let mut any_backing = false;
                        let mut ink = 0.0;
                        for y in 0..dy {
                            for x in 0..dx {
                                let px = u32::from(col) * u32::from(dx) + u32::from(x);
                                let py = u32::from(row) * u32::from(dy) + u32::from(y);
                                let sample = projected_face_uv(&sprite, projection, px, py);
                                clear_gap &= sample.is_some_and(|(uv, footprint)| {
                                    shaded || !texture.sample(uv, footprint)
                                });
                                if let Some((uv, footprint)) = sample {
                                    ink += texture.coverage(uv, footprint, f64::INFINITY)
                                        / f64::from(dx * dy);
                                }
                                any_backing |= sample.is_some();
                            }
                        }
                        clear_gap &= !shaded || ink < SHADED_MIN_INK;
                        if clear_gap {
                            assert_ne!(buf[(col, row)].symbol(), "X", "{renderer:?}");
                            let py = u32::from(row) * u32::from(dy)
                                + u32::from(renderer == Renderer::HalfBlock);
                            let material = projection
                                .sample(&sprite, u32::from(col) * u32::from(dx), py)
                                .unwrap();
                            assert_eq!(
                                buf[(col, row)],
                                source[(col, row)],
                                "{renderer:?}: stroke gap changed the actual body representation"
                            );
                            noncream_gaps += usize::from(material != 4);
                            cleared_gaps += 1;
                        }
                        if matches!(
                            renderer,
                            Renderer::Ascii | Renderer::Quadrant | Renderer::Braille
                        ) {
                            assert_eq!(
                                buf[(col, row)].bg,
                                before[(col, row)].bg,
                                "{renderer:?}: expression painted a background rectangle"
                            );
                        }
                        if !any_backing {
                            assert_eq!(buf[(col, row)], before[(col, row)], "{renderer:?}");
                            untouched += 1;
                        }
                    }
                }
                assert!(
                    cleared_gaps > 0 && noncream_gaps > 0 && untouched > 0,
                    "{renderer:?}"
                );
            }
        }
    }

    #[test]
    fn monochrome_expression_strokes_contrast_with_the_actual_body_glyph() {
        let config = Config::sample().unwrap();
        let colors = Colors::from_config(&config, Depth::Mono).unwrap();
        let sprite = Sprite::voxel_with_appearance(
            Stage::Adult,
            Expression::Happy,
            SpriteStyle::SixteenBit,
            0,
            0,
            false,
            Appearance {
                shape: 0,
                body: 4,
                accent: 11,
            },
        );
        for renderer in [Renderer::Ascii, Renderer::HalfBlock] {
            let area = Rect::new(0, 0, 96, 48);
            let projection = Projection::camera(
                &sprite,
                SolidBounds::of(&sprite).unwrap(),
                area,
                renderer,
                0,
                (500, 500),
                Camera {
                    orbit_degrees: 45,
                    view: CameraView::Face,
                    ..Camera::default()
                },
            );
            let mut before = Buffer::empty(area);
            draw_projection(&sprite, renderer, &colors, projection, area, &mut before);
            let mut after = before.clone();
            assert!(draw_camera_face(
                &sprite, renderer, &colors, projection, area, &mut after
            ));
            assert!(
                before
                    .content
                    .iter()
                    .zip(&after.content)
                    .any(|(a, b)| a.symbol() != b.symbol()),
                "{renderer:?}: monochrome face vanished into the body's glyph"
            );
            assert!(before
                .content
                .iter()
                .zip(&after.content)
                .all(|(a, b)| a.bg == b.bg));
        }
    }

    fn uv_axis(sprite: &Sprite) -> (f64, f64) {
        let mut sum = [[0.0; 3]; 2];
        for y in 0..u32::from(sprite.height()) {
            for x in 0..u32::from(sprite.width()) {
                let Some(uv) = valid_face_uv(sprite, x, y) else {
                    continue;
                };
                if !(0.25..=0.75).contains(&uv[1]) {
                    continue;
                }
                let side = if uv[0] <= 0.35 {
                    0
                } else if uv[0] >= 0.65 {
                    1
                } else {
                    continue;
                };
                sum[side][0] += f64::from(x);
                sum[side][1] += f64::from(y);
                sum[side][2] += 1.0;
            }
        }
        assert!(sum.iter().all(|s| s[2] > 0.0));
        (
            sum[1][0] / sum[1][2] - sum[0][0] / sum[0][2],
            sum[1][1] / sum[1][2] - sum[0][1] / sum[0][2],
        )
    }

    #[test]
    fn face_uvs_foreshorten_with_yaw_and_rotate_with_the_sleeping_head() {
        let make = |yaw, expression| {
            Sprite::voxel(
                Stage::Adult,
                expression,
                SpriteStyle::SixteenBit,
                0,
                yaw,
                false,
            )
        };
        let front = make(0, Expression::Happy);
        let oblique = make(60, Expression::Happy);
        let rear = make(180, Expression::Happy);
        let front_bounds = FaceTexture::bounds(&front).expect("visible frontal surface");
        let side_bounds = FaceTexture::bounds(&oblique).expect("actual oblique head surface");
        assert!(
            side_bounds.width() < front_bounds.width(),
            "UV surface must foreshorten: {front_bounds:?} / {side_bounds:?}"
        );
        assert!(FaceTexture::bounds(&rear).is_none());
        let awake_axis = uv_axis(&front);
        let sleeping_axis = uv_axis(&make(0, Expression::Sleeping));
        assert!(
            sleeping_axis.1.abs() > awake_axis.1.abs() + 1.0,
            "texture axis must roll with head: {awake_axis:?} / {sleeping_axis:?}"
        );
    }

    #[test]
    fn surface_texture_never_paints_unmapped_nose_ears_body_or_occluded_pixels() {
        let config = Config::sample().unwrap();
        let colors = Colors::from_config(&config, Depth::Indexed).unwrap();
        for yaw in [0, 35, 60] {
            let sprite = Sprite::voxel(
                Stage::Adult,
                Expression::Happy,
                SpriteStyle::SixteenBit,
                0,
                yaw,
                false,
            );
            let bounds = FaceTexture::bounds(&sprite).unwrap();
            let opaque_holes = (bounds.top as u32..bounds.bottom as u32)
                .flat_map(|y| (bounds.left as u32..bounds.right as u32).map(move |x| (x, y)))
                .filter(|&(x, y)| {
                    sprite.pixel(x, y, 1).is_some() && valid_face_uv(&sprite, x, y).is_none()
                })
                .count();
            assert!(
                opaque_holes > 0,
                "depth/anatomy exclusion control at yaw {yaw}"
            );
            for renderer in [
                Renderer::Ascii,
                Renderer::HalfBlock,
                Renderer::Quadrant,
                Renderer::Braille,
            ] {
                let area = Rect::new(2, 3, 120, 60);
                let projection = Projection::camera(
                    &sprite,
                    SolidBounds::of(&sprite).unwrap(),
                    area,
                    renderer,
                    0,
                    (500, 500),
                    Camera {
                        orbit_degrees: 45,
                        view: CameraView::Face,
                        zoom_percent: 200,
                        ..Camera::default()
                    },
                );
                let mut buf = Buffer::empty(Rect::new(0, 0, 124, 66));
                draw_projection(&sprite, renderer, &colors, projection, area, &mut buf);
                let before = buf.clone();
                assert!(draw_camera_face(
                    &sprite, renderer, &colors, projection, area, &mut buf
                ));
                let (dx, dy) = renderer.packing();
                let mut protected = 0;
                for row in area.y..area.bottom() {
                    for col in area.x..area.right() {
                        let mut valid = 0;
                        for y in 0..dy {
                            for x in 0..dx {
                                valid += usize::from(
                                    projected_face_uv(
                                        &sprite,
                                        projection,
                                        u32::from(col) * u32::from(dx) + u32::from(x),
                                        u32::from(row) * u32::from(dy) + u32::from(y),
                                    )
                                    .is_some(),
                                );
                            }
                        }
                        let mixed_pattern =
                            matches!(renderer, Renderer::Quadrant | Renderer::Braille)
                                && valid < usize::from(dx * dy);
                        if valid == 0 || mixed_pattern {
                            assert_eq!(
                                buf[(col, row)],
                                before[(col, row)],
                                "{renderer:?} yaw {yaw} unmapped surface"
                            );
                            protected += 1;
                        }
                    }
                }
                assert!(protected > 0);
            }
        }
    }

    #[test]
    fn minified_native_faces_follow_visible_head_uvs_and_camera_translation() {
        let config = Config::sample().unwrap();
        let colors = Colors::from_config(&config, Depth::Indexed).unwrap();
        let mut visible_cases = 0;
        for yaw in [0, 60] {
            let sprite = Sprite::voxel(
                Stage::Adult,
                Expression::Happy,
                SpriteStyle::SixteenBit,
                0,
                yaw,
                false,
            );
            let rear = Sprite::voxel(
                Stage::Adult,
                Expression::Happy,
                SpriteStyle::SixteenBit,
                0,
                180,
                false,
            );
            let face = sprite.face_overlay().unwrap();
            for renderer in [
                Renderer::Ascii,
                Renderer::HalfBlock,
                Renderer::Quadrant,
                Renderer::Braille,
            ] {
                let (dx, dy) = renderer.packing();
                let scale = 0.1 * f64::from(dx);
                let projection = Projection {
                    left: 40.0 * f64::from(dx) - (f64::from(face.center_x) + 0.5) * scale,
                    top: 20.0 * f64::from(dy)
                        - (f64::from(face.y) + 0.5) * scale * camera_pixel_aspect(renderer),
                    scale,
                    scale_y: scale * camera_pixel_aspect(renderer),
                };
                let area = Rect::new(0, 0, 80, 40);
                let changes = |projection: Projection| {
                    let mut before = Buffer::empty(area);
                    draw_projection(&sprite, renderer, &colors, projection, area, &mut before);
                    let mut after = before.clone();
                    let painted =
                        draw_camera_face(&sprite, renderer, &colors, projection, area, &mut after);
                    let mut marks = Vec::new();
                    for row in area.y..area.bottom() {
                        for col in area.x..area.right() {
                            if before[(col, row)] != after[(col, row)] {
                                for y in 0..dy {
                                    for x in 0..dx {
                                        assert!(
                                            projected_head_uv(
                                                &sprite,
                                                projection,
                                                u32::from(col) * u32::from(dx) + u32::from(x),
                                                u32::from(row) * u32::from(dy) + u32::from(y)
                                            )
                                            .is_some(),
                                            "{renderer:?} yaw{yaw}: minified glyph escaped visible head material"
                                        );
                                    }
                                }
                                marks.push((col, row, after[(col, row)].symbol().to_owned()));
                            }
                        }
                    }
                    assert_eq!(painted, !marks.is_empty());
                    marks
                };
                let original = changes(projection);
                visible_cases += usize::from(!original.is_empty());
                let moved = changes(Projection {
                    left: projection.left - 10.0 * f64::from(dx),
                    ..projection
                });
                let expected: Vec<_> = original
                    .iter()
                    .map(|(x, y, glyph)| (x - 10, *y, glyph.clone()))
                    .collect();
                assert_eq!(
                    moved, expected,
                    "{renderer:?} yaw{yaw}: native glyphs must move with the head, never a fixed screen label"
                );
                let mut rear_before = Buffer::empty(area);
                draw_projection(&rear, renderer, &colors, projection, area, &mut rear_before);
                let mut rear_after = rear_before.clone();
                assert!(!draw_camera_face(
                    &rear,
                    renderer,
                    &colors,
                    projection,
                    area,
                    &mut rear_after
                ));
                assert_eq!(
                    rear_after, rear_before,
                    "hidden rear face must remain unpainted"
                );
            }
        }
        assert!(
            visible_cases > 0,
            "the invariant must exercise actual minified native faces"
        );
    }

    #[test]
    fn camera_physical_aspect_and_zoom_match_across_terminal_packings() {
        let sprite = padded_body();
        let bounds = SolidBounds::of(&sprite).unwrap();
        let area = Rect::new(0, 0, 80, 40);
        let mut references = Vec::new();
        for renderer in [
            Renderer::HalfBlock,
            Renderer::Ascii,
            Renderer::Quadrant,
            Renderer::Braille,
        ] {
            let mut occupancy = Vec::new();
            for (index, zoom_percent) in [50, 75, 100, 150].into_iter().enumerate() {
                let camera = Camera {
                    zoom_percent,
                    ..Camera::default()
                };
                let projection =
                    Projection::camera(&sprite, bounds, area, renderer, 0, (500, 500), camera);
                let (dx, dy) = renderer.packing();
                let physical_x = projection.scale / f64::from(dx) / 2.0;
                let physical_y = projection.scale_y / f64::from(dy);
                assert!((physical_x - physical_y).abs() < 0.00001, "{renderer:?}");
                let buf = projected(&sprite, renderer, camera, area);
                let mut points = Vec::new();
                for y in 0..area.height {
                    for x in 0..area.width {
                        if buf[(x, y)].symbol() != " " {
                            points.push((x, y));
                        }
                    }
                }
                let width = points.iter().map(|p| p.0).max().unwrap()
                    - points.iter().map(|p| p.0).min().unwrap()
                    + 1;
                let height = points.iter().map(|p| p.1).max().unwrap()
                    - points.iter().map(|p| p.1).min().unwrap()
                    + 1;
                if renderer == Renderer::HalfBlock {
                    references.push((width, height));
                } else {
                    let (expected_w, expected_h) = references[index];
                    assert!(
                        width.abs_diff(expected_w) <= 2 && height.abs_diff(expected_h) <= 2,
                        "{renderer:?}/{zoom_percent}: {width}x{height} vs {expected_w}x{expected_h}"
                    );
                }
                if zoom_percent <= 100 {
                    let actual = f64::from(width) / 2.0 / f64::from(height);
                    assert!(
                        (actual - bounds.width() / bounds.height()).abs() < 0.05,
                        "{renderer:?}: physically stretched body at {zoom_percent}%"
                    );
                }
                occupancy.push(occupied(&buf));
            }
            assert!(
                occupancy.windows(2).all(|w| w[1] > w[0]),
                "{renderer:?}: {occupancy:?}"
            );
        }
    }

    #[test]
    fn packing_preserves_two_halfblock_colors_and_pattern_masks() {
        let colors = Colors {
            palette: (0..16).map(Color::Indexed).collect(),
            background: Color::Indexed(15),
            lighting: None,
        };
        let mut cell = ratatui::buffer::Cell::default();
        cell.set_bg(Color::Indexed(9));
        paint_pixels(
            Renderer::HalfBlock,
            &colors,
            [Some(1), Some(2), None, None, None, None, None, None],
            &mut cell,
        );
        assert_eq!(cell.symbol(), "▀");
        assert_eq!(cell.fg, Color::Indexed(1));
        assert_eq!(cell.bg, Color::Indexed(2));
        paint_pixels(
            Renderer::HalfBlock,
            &colors,
            [Some(3), Some(3), None, None, None, None, None, None],
            &mut cell,
        );
        assert_eq!(cell.symbol(), "█");
        let pixels = [Some(1), None, None, Some(2), None, Some(3), None, None];
        paint_pixels(Renderer::Quadrant, &colors, pixels, &mut cell);
        assert_eq!(
            cell.symbol(),
            quadrant([true, false, false, true]).to_string()
        );
        assert_eq!(cell.fg, Color::Indexed(1));
        paint_pixels(Renderer::Braille, &colors, pixels, &mut cell);
        assert_eq!(
            cell.symbol(),
            braille([true, false, false, true, false, true, false, false]).to_string()
        );
    }
}

#[cfg(test)]
mod voxel_world_integration_tests {
    use super::*;
    use crate::{
        capabilities::Depth,
        game::Stage,
        sprite::{Appearance, Expression, SpriteStyle},
        world::WorldRules,
    };
    #[test]
    fn all_world_orbits_and_packings_stay_inside_offset_viewport() {
        let config = Config::sample().unwrap();
        let colors = Colors::from_config(&config, Depth::Indexed).unwrap();
        for renderer in [
            Renderer::Ascii,
            Renderer::HalfBlock,
            Renderer::Quadrant,
            Renderer::Braille,
        ] {
            for (width, height) in [(1, 1), (4, 2), (12, 6), (20, 8), (80, 24), (35, 55)] {
                let full = Rect::new(0, 0, width + 6, height + 6);
                let area = Rect::new(3, 3, width, height);
                for yaw in [0, 45, 90, 135, 180, 225, 270, 315] {
                    let sprite = Sprite::voxel_with_camera(
                        Stage::Adult,
                        Expression::Happy,
                        SpriteStyle::EightBit,
                        0,
                        [yaw, 30],
                        false,
                        Appearance::default(),
                    );
                    let mut buf = Buffer::empty(full);
                    for cell in &mut buf.content {
                        cell.set_char('!');
                    }
                    RoomWidget {
                        sprite: &sprite,
                        scale: 1,
                        renderer,
                        colors: &colors,
                        expression: Expression::Happy,
                        detail: SpriteStyle::EightBit,
                        frame: 0,
                        position: (500, 700),
                        camera: Camera {
                            orbit_degrees: yaw,
                            view: CameraView::Room,
                            from_view: CameraView::Room,
                            ..Camera::default()
                        },
                        habitat: None,
                        world: Some(&WorldRules::default()),
                    }
                    .render(area, &mut buf);
                    for row in full.y..full.bottom() {
                        for col in full.x..full.right() {
                            if !area.contains((col, row).into()) {
                                assert_eq!(buf[(col, row)].symbol(), "!");
                            }
                        }
                    }
                }
            }
        }
    }
    #[test]
    fn camera_rotation_changes_real_room_geometry_without_touching_game() {
        let config = Config::sample().unwrap();
        let game = crate::game::Game::new(&config.game, 903);
        let before = game.clone();
        let colors = Colors::from_config(&config, Depth::Indexed).unwrap();
        let area = Rect::new(0, 0, 80, 30);
        let mut frames = Vec::new();
        for yaw in [45, 135, 225, 315] {
            let sprite = Sprite::voxel_with_camera(
                Stage::Baby,
                Expression::Happy,
                SpriteStyle::SixteenBit,
                0,
                [yaw, 30],
                false,
                Appearance::default(),
            );
            let mut buf = Buffer::empty(area);
            RoomWidget {
                sprite: &sprite,
                scale: 1,
                renderer: Renderer::HalfBlock,
                colors: &colors,
                expression: Expression::Happy,
                detail: SpriteStyle::SixteenBit,
                frame: 0,
                position: (300, 700),
                camera: Camera {
                    orbit_degrees: yaw,
                    view: CameraView::Room,
                    from_view: CameraView::Room,
                    ..Camera::default()
                },
                habitat: Some(HabitatView {
                    state: &game.habitat,
                    rules: &config.habitat,
                    wins: game.wins,
                    age: game.age_ticks,
                }),
                world: Some(&config.world),
            }
            .render(area, &mut buf);
            frames.push(buf);
        }
        assert_ne!(frames[0], frames[1]);
        assert_ne!(frames[1], frames[2]);
        assert_eq!(game, before);
    }
}

#[cfg(test)]
mod packed_world_face_regressions {
    use super::*;
    use crate::{
        capabilities::Depth,
        game::Stage,
        sprite::{Appearance, Expression, SpriteStyle},
    };
    #[test]
    fn native_and_atlas_faces_preserve_both_orientations_of_world_owned_halfblock_cells() {
        let config = Config::sample().unwrap();
        let colors = Colors::from_config(&config, Depth::Indexed).unwrap();
        let sprite = Sprite::voxel_with_camera(
            Stage::Adult,
            Expression::Happy,
            SpriteStyle::SixteenBit,
            0,
            [0, 30],
            false,
            Appearance::default(),
        );
        for (w, h) in [(80, 30), (160, 90)] {
            let area = Rect::new(0, 0, w, h);
            let projection = Projection::camera(
                &sprite,
                SolidBounds::of(&sprite).unwrap(),
                area,
                Renderer::HalfBlock,
                0,
                (500, 500),
                Camera {
                    view: CameraView::Face,
                    from_view: CameraView::Face,
                    ..Camera::default()
                },
            );
            for glyph in ['▀', '▄'] {
                let mut original = Buffer::empty(area);
                for cell in &mut original.content {
                    cell.set_char(glyph).set_fg(Color::Red).set_bg(Color::Blue);
                }
                let mut all = original.clone();
                let mut owners = vec![true; usize::from(w) * usize::from(h)];
                assert!(draw_world_face(
                    &sprite,
                    Renderer::HalfBlock,
                    &colors,
                    projection,
                    (area, &owners),
                    &mut all
                ));
                let changed = all
                    .content
                    .iter()
                    .zip(&original.content)
                    .enumerate()
                    .filter_map(|(i, (a, b))| (a != b).then_some(i))
                    .take(16)
                    .collect::<Vec<_>>();
                assert!(!changed.is_empty());
                for index in changed {
                    owners[index] = false;
                    let mut actual = original.clone();
                    draw_world_face(
                        &sprite,
                        Renderer::HalfBlock,
                        &colors,
                        projection,
                        (area, &owners),
                        &mut actual,
                    );
                    assert_eq!(
                        actual.content[index], original.content[index],
                        "mixed cell {index}, {w}x{h}, {glyph}"
                    );
                    owners[index] = true;
                }
            }
        }
    }
}

#[cfg(test)]
mod legible_silhouette_tests {
    use super::*;
    use crate::{
        game::Stage,
        sprite::{Appearance, Expression, SpriteStyle},
    };

    fn light() -> crate::lighting::VoxelLighting {
        let mut light = Config::sample().unwrap().voxel_lighting;
        light.enabled = true;
        light
    }

    fn sample(
        normal: [f32; 3],
        occlusion: u8,
        screen_edge: bool,
        texture: Option<(u32, u32)>,
    ) -> Option<SurfaceLight> {
        Some(SurfaceLight {
            normal,
            occlusion,
            edge: screen_edge,
            screen_edge,
            texture,
        })
    }

    fn one(surface: Option<SurfaceLight>) -> [Option<SurfaceLight>; 8] {
        let mut samples = [None; 8];
        samples[0] = surface;
        samples
    }

    #[test]
    fn dark_silhouettes_stay_distinct_from_the_scene_and_interiors_keep_their_light() {
        let light = light();
        let minimum = u32::from(light.outline_percent) * 255 / 100;
        let normal = [0.0, -1.0, 0.0];
        let pixels = [Some(1), None, None, None, None, None, None, None];
        for depth in [Depth::Rgb, Depth::Indexed, Depth::Ansi] {
            // A dark body over the vendor scene: the low-contrast defect case.
            let mut palette = Config::sample()
                .unwrap()
                .palette
                .iter()
                .map(|text| quantize(parse_rgb(text).unwrap(), depth))
                .collect::<Vec<_>>();
            palette[1] = quantize([0x2A, 0x26, 0x40], depth);
            let colors = Colors {
                palette,
                background: quantize([0x11, 0x18, 0x27], depth),
                lighting: Some(light.clone()),
            };
            let scene = luminance(colors.background).unwrap();
            let unguarded =
                shade_terminal_color(&light, colors.ink(1), light.brightness(normal, 60, true));
            assert!(
                luminance(unguarded).unwrap().abs_diff(scene) < minimum,
                "{depth:?}: the fixture must reproduce the defect without the silhouette rule"
            );
            let mut edge = ratatui::buffer::Cell::default();
            paint_lit_pixels(
                Renderer::Ascii,
                &colors,
                pixels,
                one(sample(normal, 60, true, None)),
                [false; 8],
                &mut edge,
            );
            assert!(
                luminance(edge.fg).unwrap().abs_diff(scene) >= minimum,
                "{depth:?}: silhouette {:?} against scene {:?}",
                edge.fg,
                colors.background
            );
            assert_ne!(edge.fg, colors.background);
            let mut interior = ratatui::buffer::Cell::default();
            paint_lit_pixels(
                Renderer::Ascii,
                &colors,
                pixels,
                one(sample(normal, 60, false, None)),
                [false; 8],
                &mut interior,
            );
            assert_eq!(
                interior.fg,
                shade_terminal_color(&light, colors.ink(1), light.brightness(normal, 60, false)),
                "{depth:?}: only silhouette samples may change"
            );
        }
    }

    #[test]
    fn monochrome_silhouettes_use_the_densest_ramp_glyph() {
        let light = light();
        let colors = Colors {
            palette: vec![Color::Reset; 2],
            background: Color::Reset,
            lighting: Some(light.clone()),
        };
        let normal = [0.0, -1.0, 0.0];
        let pixels = [Some(1), None, None, None, None, None, None, None];
        let dense = light.glyph(180).to_string();
        let faint = light
            .glyph(light.brightness(normal, 100, false))
            .to_string();
        assert_ne!(
            faint, dense,
            "the shadowed fixture must be faint off the edge"
        );
        for renderer in [Renderer::Ascii, Renderer::HalfBlock] {
            let paint = |screen_edge: bool, ink: [bool; 8]| {
                let mut cell = ratatui::buffer::Cell::default();
                paint_lit_pixels(
                    renderer,
                    &colors,
                    pixels,
                    one(sample(normal, 100, screen_edge, None)),
                    ink,
                    &mut cell,
                );
                cell.symbol().to_string()
            };
            assert_eq!(paint(true, [false; 8]), dense, "{renderer:?} silhouette");
            assert_eq!(paint(false, [false; 8]), faint, "{renderer:?} interior");
            let mut ink = [false; 8];
            ink[0] = true;
            assert_ne!(
                paint(true, ink),
                dense,
                "{renderer:?}: face ink keeps its own glyph"
            );
        }
    }

    #[test]
    fn magnified_uniform_voxels_gain_shadow_texture_only_where_shaded() {
        let light = light();
        let colors = Colors {
            palette: vec![Color::Rgb(12, 16, 24), Color::Rgb(120, 80, 40)],
            background: Color::Rgb(1, 2, 3),
            lighting: Some(light.clone()),
        };
        let ramp: Vec<char> = light.ascii_ramp.chars().collect();
        let textured = |symbol: &str| symbol.chars().next().is_some_and(|c| ramp.contains(&c));
        let shadow = [0.0, -1.0, 0.0];
        let lit = [-0.45, 0.65, 0.45];
        for renderer in [Renderer::HalfBlock, Renderer::Quadrant, Renderer::Braille] {
            let (dx, dy) = renderer.packing();
            let count = usize::from(dx * dy);
            let full: [Option<u8>; 8] = std::array::from_fn(|i| (i < count).then_some(1));
            let voxel = |normal: [f32; 3], occlusion: u8, texture: Option<(u32, u32)>| {
                let samples: [Option<SurfaceLight>; 8] = std::array::from_fn(|i| {
                    if i < count {
                        sample(normal, occlusion, false, texture)
                    } else {
                        None
                    }
                });
                samples
            };
            let paint =
                |pixels: [Option<u8>; 8], surface: [Option<SurfaceLight>; 8], ink: [bool; 8]| {
                    let mut cell = ratatui::buffer::Cell::default();
                    paint_lit_pixels(renderer, &colors, pixels, surface, ink, &mut cell);
                    cell
                };
            let cell = paint(full, voxel(shadow, 80, Some((4, 4))), [false; 8]);
            assert!(
                textured(cell.symbol()),
                "{renderer:?}: shaded voxel {:?}",
                cell.symbol()
            );
            let body =
                shade_terminal_color(&light, colors.ink(1), light.brightness(shadow, 80, false));
            assert_eq!(
                cell.bg, body,
                "{renderer:?}: the surface stays the cell background"
            );
            assert_ne!(
                cell.fg, cell.bg,
                "{renderer:?}: the shading glyph is visible"
            );
            // Negative controls: each missing permission leaves the voxel clean.
            let mut mixed = voxel(shadow, 80, Some((4, 4)));
            mixed[count - 1] = sample(shadow, 80, false, Some((4, 5)));
            let mut partial = full;
            partial[count - 1] = None;
            let mut edge = voxel(shadow, 80, Some((4, 4)));
            edge[0] = sample(shadow, 80, true, Some((4, 4)));
            for (label, cell) in [
                (
                    "lit face",
                    paint(full, voxel(lit, 0, Some((4, 4))), [false; 8]),
                ),
                (
                    "protected head",
                    paint(full, voxel(shadow, 80, None), [false; 8]),
                ),
                (
                    "face ink",
                    paint(full, voxel(shadow, 80, Some((4, 4))), [true; 8]),
                ),
                ("two source pixels", paint(full, mixed, [false; 8])),
                (
                    "partial coverage",
                    paint(partial, voxel(shadow, 80, Some((4, 4))), [false; 8]),
                ),
                ("silhouette", paint(full, edge, [false; 8])),
            ] {
                assert!(
                    !textured(cell.symbol()),
                    "{renderer:?} {label}: {:?}",
                    cell.symbol()
                );
            }
        }
    }

    #[test]
    fn every_species_keeps_a_bounded_visible_silhouette_and_texture_never_reaches_faces() {
        let config = Config::sample().unwrap();
        let dense = light().glyph(180).to_string();
        let ramp: Vec<char> = light().ascii_ramp.chars().collect();
        let mut textured_somewhere = false;
        for shape in 0..16 {
            for stage in [Stage::Baby, Stage::Child, Stage::Adult] {
                let appearance = Appearance {
                    shape,
                    body: shape,
                    accent: (shape + 5) % 16,
                };
                let sprite = Sprite::voxel_with_appearance(
                    stage,
                    Expression::Happy,
                    SpriteStyle::SixteenBit,
                    0,
                    0,
                    false,
                    appearance,
                );
                let bounds = SolidBounds::of(&sprite).unwrap();
                for (width, height, zoom_percent) in
                    [(10, 4, 100), (20, 9, 100), (48, 24, 100), (48, 24, 400)]
                {
                    for renderer in [
                        Renderer::Ascii,
                        Renderer::HalfBlock,
                        Renderer::Quadrant,
                        Renderer::Braille,
                    ] {
                        for depth in [Depth::Mono, Depth::Ansi, Depth::Rgb] {
                            let label = format!(
                                "shape {shape} {stage:?} {width}x{height}@{zoom_percent} {renderer:?} {depth:?}"
                            );
                            let colors = Colors::from_config(&config, depth).unwrap();
                            let area = Rect::new(2, 1, width, height);
                            let whole = Rect::new(0, 0, width + 4, height + 2);
                            let mut buf = Buffer::empty(whole);
                            for y in whole.y..whole.bottom() {
                                for x in whole.x..whole.right() {
                                    if !area.contains((x, y).into()) {
                                        buf[(x, y)].set_char('!');
                                    }
                                }
                            }
                            let baseline = buf.clone();
                            let projection = Projection::camera(
                                &sprite,
                                bounds,
                                area,
                                renderer,
                                0,
                                (500, 500),
                                Camera {
                                    zoom_percent,
                                    ..Camera::default()
                                },
                            );
                            draw_projection(&sprite, renderer, &colors, projection, area, &mut buf);
                            for y in whole.y..whole.bottom() {
                                for x in whole.x..whole.right() {
                                    if !area.contains((x, y).into()) {
                                        assert_eq!(
                                            buf[(x, y)],
                                            baseline[(x, y)],
                                            "{label}: bounded"
                                        );
                                    }
                                }
                            }
                            let occupied = |x: u16, y: u16| buf[(x, y)].symbol() != " ";
                            assert!(
                                (area.y..area.bottom())
                                    .any(|y| (area.x..area.right()).any(|x| occupied(x, y))),
                                "{label}: the Mon must be visible"
                            );
                            let (dx, dy) = renderer.packing();
                            for y in area.y..area.bottom() {
                                for x in area.x..area.right() {
                                    if depth == Depth::Mono
                                        && renderer == Renderer::Ascii
                                        && occupied(x, y)
                                    {
                                        let boundary = [
                                            (x.wrapping_sub(1), y),
                                            (x + 1, y),
                                            (x, y.wrapping_sub(1)),
                                            (x, y + 1),
                                        ]
                                        .into_iter()
                                        .any(|(nx, ny)| {
                                            area.contains((nx, ny).into()) && !occupied(nx, ny)
                                        });
                                        let lit = projected_surface_light(
                                            &sprite,
                                            projection,
                                            u32::from(x),
                                            u32::from(y),
                                        )
                                        .is_some();
                                        if boundary && lit {
                                            assert_eq!(
                                                buf[(x, y)].symbol(),
                                                dense,
                                                "{label}: mono boundary at {x},{y}"
                                            );
                                        }
                                    }
                                    let symbol = buf[(x, y)].symbol();
                                    let shaded =
                                        symbol.chars().next().is_some_and(|c| ramp.contains(&c));
                                    if renderer == Renderer::Ascii
                                        || depth == Depth::Mono
                                        || !shaded
                                    {
                                        continue;
                                    }
                                    textured_somewhere = true;
                                    for sy in 0..dy {
                                        for sx in 0..dx {
                                            let px = u32::from(x) * u32::from(dx) + u32::from(sx);
                                            let py = u32::from(y) * u32::from(dy) + u32::from(sy);
                                            let (ux, uy) = projection
                                                .source_pixel(&sprite, px, py)
                                                .unwrap_or_else(|| panic!("{label}: texture outside the Mon at {x},{y}"));
                                            let index = sprite.pixel(ux, uy, 1);
                                            assert!(
                                                index.is_some(),
                                                "{label}: texture outside the Mon at {x},{y}"
                                            );
                                            assert!(
                                                sprite.head_uv(ux, uy).is_none(),
                                                "{label}: texture on the protected face at {x},{y}"
                                            );
                                            for (nx, ny) in [
                                                (ux.wrapping_sub(1), uy),
                                                (ux + 1, uy),
                                                (ux, uy.wrapping_sub(1)),
                                                (ux, uy + 1),
                                            ] {
                                                assert_eq!(
                                                    sprite.pixel(nx, ny, 1),
                                                    index,
                                                    "{label}: texture on a feature at {x},{y}"
                                                );
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
            // The overlay may not reveal a face the camera cannot see.
            let rear = Sprite::voxel_with_appearance(
                Stage::Adult,
                Expression::Happy,
                SpriteStyle::SixteenBit,
                0,
                180,
                false,
                Appearance {
                    shape,
                    body: shape,
                    accent: (shape + 5) % 16,
                },
            );
            // (RoomWidget's separate mood caption may name the mood; only the
            // body and face paint are under test here.)
            for renderer in [Renderer::Ascii, Renderer::HalfBlock] {
                let colors = Colors::from_config(&config, Depth::Rgb).unwrap();
                let area = Rect::new(0, 0, 48, 24);
                let mut buf = Buffer::empty(area);
                let projection = Projection::camera(
                    &rear,
                    SolidBounds::of(&rear).unwrap(),
                    area,
                    renderer,
                    0,
                    (500, 500),
                    Camera {
                        view: CameraView::Face,
                        from_view: CameraView::Face,
                        zoom_percent: 400,
                        ..Camera::default()
                    },
                );
                draw_projection(&rear, renderer, &colors, projection, area, &mut buf);
                draw_front_face(&rear, renderer, &colors, projection, area, &mut buf);
                let text: String = buf.content.iter().map(|c| c.symbol()).collect();
                assert!(
                    !text.contains(Expression::Happy.kaomoji(0)),
                    "shape {shape} {renderer:?}: rear view revealed the face"
                );
            }
        }
        assert!(
            textured_somewhere,
            "magnified views must show shadow texture"
        );
    }
}

#[cfg(test)]
mod kaomoji_scale_tests {
    use super::*;
    use crate::{
        game::Stage,
        sprite::{Appearance, Expression, SpriteStyle},
    };

    /// Ink extent of the whole text as width/height on a face plane of the
    /// given physical aspect (width `aspect`, height 1).
    fn ink_ratio(texture: FaceTexture, aspect: f64) -> f64 {
        let (n, m) = (480usize, 240usize);
        let footprint = [1.0 / n as f64, 1.0 / m as f64];
        let (mut u0, mut u1, mut v0, mut v1) = (1.0f64, 0.0f64, 1.0f64, 0.0f64);
        for j in 0..m {
            for i in 0..n {
                let uv = [(i as f64 + 0.5) / n as f64, (j as f64 + 0.5) / m as f64];
                if texture.sample(uv, footprint) {
                    u0 = u0.min(uv[0]);
                    u1 = u1.max(uv[0]);
                    v0 = v0.min(uv[1]);
                    v1 = v1.max(uv[1]);
                }
            }
        }
        assert!(u1 > u0 && v1 > v0, "no ink sampled");
        (u1 - u0) * aspect / (v1 - v0)
    }

    #[test]
    fn face_atlas_keeps_glyph_proportions_on_the_face_plane() {
        let text = "(o_O)";
        // The bitmap's own ink box: 37 columns by 9 rows.
        let ink = 37.0 / 9.0;
        for aspect in [0.8, 1.2, 1.6, 2.5, 6.0] {
            let fitted = FaceTexture {
                text,
                aspect: Some(aspect),
            };
            let ratio = ink_ratio(fitted, aspect);
            assert!(
                (ratio / ink - 1.0).abs() < 0.12,
                "aspect {aspect}: fitted ink ratio {ratio} vs {ink}"
            );
            let fraction = fitted.text_fraction();
            assert!(fraction.iter().all(|f| *f <= 1.0 + 1e-9), "{fraction:?}");
            assert!(
                fraction.iter().any(|f| (f - 1.0).abs() < 1e-9),
                "the box fills one axis: {fraction:?}"
            );
            // Negative control: the legacy map stretches the box to the UV
            // square, so tall eyes appear wherever the plane is not its shape.
            if aspect <= 1.6 || aspect >= 6.0 {
                let stretched = ink_ratio(FaceTexture { text, aspect: None }, aspect);
                assert!(
                    (stretched / ink - 1.0).abs() > 0.3,
                    "aspect {aspect}: legacy ratio {stretched} should distort"
                );
            }
        }
    }

    #[test]
    fn authorized_face_planes_interpolate_monotonically_whatever_their_texel_step() {
        // A coarse but fully authorized plane must interpolate inside every
        // texel whatever its UV step, including steps past 0.5 on a two-texel
        // face: continuity comes from authority, not from a step cutoff.
        for (rows, step) in [(3, 0.3), (3, 0.45), (2, 0.6), (2, 0.8)] {
            let columns = 12;
            let sprite = Sprite::synthetic_face_plane(
                columns,
                rows,
                [1.0 / f32::from(columns), step],
                1,
                "(o_o)",
            );
            let magnify = 8.0;
            let projection = Projection {
                left: 0.0,
                top: 0.0,
                scale: magnify,
                scale_y: magnify,
            };
            let x = (f64::from(columns / 2) + 1.5) * magnify;
            let rows_on_face = (u32::from(rows) * magnify as u32) as usize;
            let v: Vec<f64> = (0..rows_on_face)
                .map(|i| {
                    let y = magnify as u32 + i as u32;
                    projected_face_uv(&sprite, projection, x as u32, y)
                        .expect("every face pixel is authorized")
                        .0[1]
                })
                .collect();
            let per_pixel = f64::from(step) / magnify;
            for (i, pair) in v.windows(2).enumerate() {
                let rise = pair[1] - pair[0];
                assert!(rise >= -1e-9, "step {step}: v falls at row {i}: {pair:?}");
                let interior = pair.iter().all(|v| *v > 1e-9 && *v < 1.0 - 1e-9);
                if interior {
                    assert!(
                        (rise - per_pixel).abs() <= per_pixel * 0.25 + 1e-6,
                        "step {step}: row {i} rises {rise:.4}, the plane rises {per_pixel:.4} per pixel"
                    );
                }
            }
        }
    }

    #[test]
    fn eight_bit_faces_interpolate_uv_across_the_cell_rows_of_one_texel() {
        // Eight-bit sprites are 24x24, so a face texel spans several cell rows
        // at face zoom. Wherever the real sprite measures a vertical UV slope
        // (on the texel or an authorized neighbour beside it), rows inside one
        // texel must sample distinct glyph bands, or every intermediate face
        // smears into vertical bars. A genuinely grazing facet (no measured
        // slope) is not required to move.
        let mut eligible_total = 0;
        for shape in 0..16u8 {
            let appearance = Appearance {
                shape,
                body: shape,
                accent: (shape + 5) % 16,
            };
            for yaw in [0, 45] {
                let sprite = Sprite::voxel_with_appearance(
                    Stage::Baby,
                    Expression::Surprised,
                    SpriteStyle::EightBit,
                    0,
                    yaw,
                    false,
                    appearance,
                );
                let renderer = Renderer::Ascii;
                let area = Rect::new(0, 0, 40, 19);
                let projection = Projection::camera(
                    &sprite,
                    SolidBounds::of(&sprite).unwrap(),
                    area,
                    renderer,
                    0,
                    (500, 500),
                    Camera {
                        orbit_degrees: yaw,
                        view: CameraView::Face,
                        from_view: CameraView::Face,
                        zoom_percent: 150,
                        transition_percent: 100,
                    },
                );
                assert!(
                    projection.scale_y >= 2.0,
                    "shape {shape} yaw {yaw}: texel spans rows"
                );
                let vertical_slope = |sx: u32, sy: u32| {
                    let here = valid_face_uv(&sprite, sx, sy)?;
                    [sy.checked_sub(1), Some(sy + 1)]
                        .into_iter()
                        .flatten()
                        .filter_map(|y| valid_face_uv(&sprite, sx, y))
                        .map(|next| (next[1] - here[1]).abs())
                        .reduce(f64::max)
                };
                // The texel's own vertical slope where it has a vertical face neighbour
                // (zero on a grazing facet), else the adjacent texel's: the planar borrow.
                let measured = |(sx, sy): (u32, u32)| {
                    std::iter::once(sx)
                        .chain(
                            [Some(sx + 1), sx.checked_sub(1)]
                                .into_iter()
                                .flatten()
                                .filter(|x| valid_face_uv(&sprite, *x, sy).is_some()),
                        )
                        .find_map(|x| vertical_slope(x, sy))
                        .is_some_and(|slope| slope > 1e-6)
                };
                let (mut eligible, mut moving) = (0, 0);
                for row in 0..u32::from(area.height) - 1 {
                    for col in 0..u32::from(area.width) {
                        let texel = projection.source_pixel(&sprite, col, row);
                        if texel.is_none()
                            || texel != projection.source_pixel(&sprite, col, row + 1)
                        {
                            continue;
                        }
                        let (Some(a), Some(b)) = (
                            projected_face_uv(&sprite, projection, col, row),
                            projected_face_uv(&sprite, projection, col, row + 1),
                        ) else {
                            continue;
                        };
                        // Clamped to the UV border lies outside every glyph.
                        let border = |v: f64| v <= 0.0 || v >= 1.0;
                        if border(a.0[1]) || border(b.0[1]) || !measured(texel.unwrap()) {
                            continue;
                        }
                        eligible += 1;
                        moving += usize::from((b.0[1] - a.0[1]).abs() > 1e-6);
                    }
                }
                assert!(
                    eligible > 0,
                    "shape {shape} yaw {yaw}: no texel with a measured slope"
                );
                assert_eq!(
                    moving, eligible,
                    "shape {shape} yaw {yaw}: rows inside one sloped texel that keep their v"
                );
                eligible_total += eligible;
            }
        }
        assert!(
            eligible_total > 1000,
            "only {eligible_total} eligible row pairs"
        );
    }

    #[test]
    fn face_zoom_keeps_the_expression_in_view_from_100_to_400_percent() {
        // The close-up frame spans the ears to the chin, so zooming around its
        // centre slid the expression out of view (live 400% showed body only).
        let config = Config::sample().unwrap();
        let colors = Colors::from_config(&config, Depth::Indexed).unwrap();
        let mut cases = 0;
        for stage in [Stage::Baby, Stage::Child, Stage::Adult] {
            for style in [SpriteStyle::EightBit, SpriteStyle::SixteenBit] {
                for shape in 0..16u8 {
                    let appearance = Appearance {
                        shape,
                        body: shape,
                        accent: (shape + 5) % 16,
                    };
                    for yaw in [0, 45] {
                        let sprite = Sprite::voxel_with_appearance(
                            stage,
                            Expression::Happy,
                            style,
                            0,
                            yaw,
                            false,
                            appearance,
                        );
                        let anchor = face_anchor(&sprite).expect("a front view maps its face");
                        for renderer in [Renderer::Ascii, Renderer::HalfBlock] {
                            for (width, height) in [(40, 19), (80, 24)] {
                                let area = Rect::new(0, 0, width, height);
                                let project = |zoom_percent| {
                                    Projection::camera(
                                        &sprite,
                                        SolidBounds::of(&sprite).unwrap(),
                                        area,
                                        renderer,
                                        0,
                                        (500, 500),
                                        Camera {
                                            orbit_degrees: yaw,
                                            view: CameraView::Face,
                                            from_view: CameraView::Face,
                                            zoom_percent,
                                            transition_percent: 100,
                                        },
                                    )
                                };
                                let point = |zoom_percent| {
                                    project(zoom_percent).cell(anchor.0, anchor.1, renderer)
                                };
                                let label = format!(
                                    "{stage:?} {style:?} shape {shape} yaw {yaw} {renderer:?} {width}x{height}"
                                );
                                // No jump where the pivot starts.
                                let (a, b) = (point(100), point(101));
                                assert!(
                                    (a.0 - b.0).abs() < 0.5 && (a.1 - b.1).abs() < 0.5,
                                    "{label}: pivot jumps {a:?} -> {b:?}"
                                );
                                let centre = (f64::from(width) / 2.0, f64::from(height) / 2.0);
                                let last = point(MAX_ZOOM_PERCENT);
                                assert!(
                                    (last.0 - centre.0).abs() < 0.5 && (last.1 - centre.1).abs() < 0.5,
                                    "{label}: 400% face centre {last:?}, viewport centre {centre:?}"
                                );
                                for zoom_percent in (100..=MAX_ZOOM_PERCENT).step_by(50) {
                                    let (x, y) = point(zoom_percent);
                                    assert!(
                                        (0.0..f64::from(width)).contains(&x)
                                            && (0.0..f64::from(height)).contains(&y),
                                        "{label} @{zoom_percent}: face centre {x:.1},{y:.1} out of view"
                                    );
                                    let projection = project(zoom_percent);
                                    let mut body = Buffer::empty(area);
                                    draw_projection(
                                        &sprite, renderer, &colors, projection, area, &mut body,
                                    );
                                    let mut face = body.clone();
                                    draw_camera_face(
                                        &sprite, renderer, &colors, projection, area, &mut face,
                                    );
                                    // Expression strokes, never a caption: only
                                    // the face pass may change these cells.
                                    let inked = body
                                        .content
                                        .iter()
                                        .zip(&face.content)
                                        .filter(|(a, b)| a != b)
                                        .count();
                                    assert!(
                                        inked >= 3,
                                        "{label} @{zoom_percent}: only {inked} face cells in view"
                                    );
                                    cases += 1;
                                }
                            }
                        }
                    }
                }
            }
        }
        assert_eq!(cases, 3 * 2 * 16 * 2 * 2 * 2 * 7);
    }

    #[test]
    fn face_ink_separates_from_the_head_under_its_strokes_lit_or_unlit() {
        let mut substituted = [0usize; 2];
        for (lit, enabled) in [(0, true), (1, false)] {
            let mut config = Config::sample().unwrap();
            config.voxel_lighting.enabled = enabled;
            for depth in [Depth::Ansi, Depth::Indexed, Depth::Rgb] {
                let colors = Colors::from_config(&config, depth).unwrap();
                let lighting = colors.lighting.clone().unwrap();
                assert_eq!(lighting.enabled, enabled);
                let minimum = outline_separation(&lighting);
                for stage in [Stage::Baby, Stage::Child, Stage::Adult] {
                    for style in [SpriteStyle::EightBit, SpriteStyle::SixteenBit] {
                        for shape in 0..16u8 {
                            let sprite = Sprite::voxel_with_appearance(
                                stage,
                                Expression::Happy,
                                style,
                                0,
                                0,
                                false,
                                Appearance {
                                    shape,
                                    body: shape,
                                    accent: (shape + 5) % 16,
                                },
                            );
                            for region in [InkRegion::NativeBand, InkRegion::TextBox] {
                                let head = face_head_colors(&sprite, &colors, &lighting, region);
                                assert!(!head.is_empty(), "{stage:?} {style:?} shape {shape}");
                                let legible_on = |candidate: Color| {
                                    head.iter()
                                        .filter(|(seen, _)| {
                                            *seen != candidate
                                                && luminance(*seen)
                                                    .unwrap()
                                                    .abs_diff(luminance(candidate).unwrap())
                                                    >= minimum
                                        })
                                        .count()
                                };
                                let ink = face_ink(&sprite, &colors, region);
                                let label = format!(
                                    "lit={enabled} {depth:?} {stage:?} {style:?} shape {shape} {region:?}"
                                );
                                let best = colors
                                    .palette
                                    .iter()
                                    .map(|candidate| legible_on(*candidate))
                                    .max()
                                    .unwrap();
                                assert!(
                                    legible_on(ink) * 2 >= head.len() || legible_on(ink) >= best,
                                    "{label}: {ink:?} reads on {}/{} texels, a palette ink on {best}",
                                    legible_on(ink),
                                    head.len()
                                );
                                // The dark ink stays wherever it already reads.
                                if legible_on(colors.ink(0)) * 2 >= head.len() {
                                    assert_eq!(ink, colors.ink(0), "{label}");
                                } else {
                                    substituted[lit] += 1;
                                }
                            }
                        }
                    }
                }
            }
        }
        assert!(
            substituted.iter().all(|n| *n > 0),
            "lit and unlit heads must both exercise the substitute ink: {substituted:?}"
        );
    }

    #[test]
    fn rendered_face_cells_read_against_the_head_they_are_drawn_on() {
        // Measures the actual buffer: each face-glyph cell's ink against what is
        // under it (the half-block cell's body background, the ASCII body
        // glyph's colour), with lighting enabled and disabled.
        let named = [
            (false, Depth::Ansi, Stage::Baby, SpriteStyle::EightBit, 0),
            (true, Depth::Indexed, Stage::Baby, SpriteStyle::EightBit, 6),
            (true, Depth::Rgb, Stage::Baby, SpriteStyle::EightBit, 10),
            (
                false,
                Depth::Indexed,
                Stage::Baby,
                SpriteStyle::EightBit,
                10,
            ),
        ];
        let mut named_seen = [false; 4];
        for enabled in [true, false] {
            let mut config = Config::sample().unwrap();
            config.voxel_lighting.enabled = enabled;
            for depth in [Depth::Ansi, Depth::Indexed, Depth::Rgb] {
                let colors = Colors::from_config(&config, depth).unwrap();
                let minimum = outline_separation(colors.lighting.as_ref().unwrap());
                let (mut cells, mut legible) = (0usize, 0usize);
                for stage in [Stage::Baby, Stage::Adult] {
                    for style in [SpriteStyle::EightBit, SpriteStyle::SixteenBit] {
                        for shape in 0..16u8 {
                            let sprite = Sprite::voxel_with_appearance(
                                stage,
                                Expression::Happy,
                                style,
                                0,
                                0,
                                false,
                                Appearance {
                                    shape,
                                    body: shape,
                                    accent: (shape + 5) % 16,
                                },
                            );
                            let (mut case_cells, mut case_legible) = (0usize, 0usize);
                            for renderer in [Renderer::Ascii, Renderer::HalfBlock] {
                                for zoom_percent in [100, 150, 200, 300] {
                                    let area = Rect::new(0, 0, 40, 19);
                                    let projection = Projection::camera(
                                        &sprite,
                                        SolidBounds::of(&sprite).unwrap(),
                                        area,
                                        renderer,
                                        0,
                                        (500, 500),
                                        Camera {
                                            orbit_degrees: 0,
                                            view: CameraView::Face,
                                            from_view: CameraView::Face,
                                            zoom_percent,
                                            transition_percent: 100,
                                        },
                                    );
                                    let mut body = Buffer::empty(area);
                                    draw_projection(
                                        &sprite, renderer, &colors, projection, area, &mut body,
                                    );
                                    let mut face = body.clone();
                                    draw_camera_face(
                                        &sprite, renderer, &colors, projection, area, &mut face,
                                    );
                                    for (before, after) in body.content.iter().zip(&face.content) {
                                        let packed_pixels = matches!(
                                            after.symbol(),
                                            "\u{2580}" | "\u{2584}" | "\u{2588}" | " "
                                        );
                                        if before == after || packed_pixels {
                                            continue;
                                        }
                                        let under = if renderer == Renderer::HalfBlock {
                                            before.bg
                                        } else {
                                            before.fg
                                        };
                                        let (Some(ink), Some(under)) =
                                            (luminance(after.fg), luminance(under))
                                        else {
                                            continue;
                                        };
                                        case_cells += 1;
                                        case_legible += usize::from(ink.abs_diff(under) >= minimum);
                                    }
                                }
                            }
                            cells += case_cells;
                            legible += case_legible;
                            for (i, case) in named.iter().enumerate() {
                                if *case == (enabled, depth, stage, style, shape) {
                                    named_seen[i] = true;
                                    assert!(
                                        case_cells > 0 && case_legible * 2 >= case_cells,
                                        "lit={enabled} {depth:?} {stage:?} {style:?} shape {shape}: {case_legible}/{case_cells} legible face cells"
                                    );
                                }
                            }
                        }
                    }
                }
                assert!(
                    legible * 100 >= cells * 95,
                    "lit={enabled} {depth:?}: only {legible}/{cells} face cells reach the outline separation"
                );
            }
        }
        assert!(named_seen.iter().all(|seen| *seen));
    }

    #[test]
    fn face_lod_tiers_are_ordered_and_keep_the_legacy_atlas_switch() {
        for text in ["(o_O)", "(^_^)", "T_T"] {
            for aspect in [None, Some(0.8), Some(1.6), Some(4.0)] {
                let texture = FaceTexture { text, aspect };
                let rank = |lod| match lod {
                    FaceLod::Native => 0,
                    FaceLod::Shaded => 1,
                    FaceLod::Atlas => 2,
                };
                for step_rows in 0..40 {
                    let rows = f64::from(step_rows) * 0.5;
                    let mut previous = 0;
                    for step_columns in 0..120 {
                        let columns = f64::from(step_columns) * 0.5;
                        let lod = face_lod(texture, columns, rows, true);
                        assert!(
                            rank(lod) >= previous,
                            "{text} {aspect:?} {columns}x{rows}: {lod:?}"
                        );
                        previous = rank(lod);
                        if aspect.is_none() {
                            let legacy = columns >= text.len() as f64 * 5.0 && rows >= 4.5;
                            assert_eq!(lod == FaceLod::Atlas, legacy, "{text} {columns}x{rows}");
                        }
                        // One pixel per cell shades wherever packed pixels would
                        // shade or take the atlas, and never takes the atlas.
                        let cells = face_lod(texture, columns, rows, false);
                        assert_ne!(cells, FaceLod::Atlas, "{text} {columns}x{rows}");
                        assert_eq!(cells == FaceLod::Native, lod == FaceLod::Native);
                    }
                }
            }
        }
    }

    #[test]
    fn intermediate_faces_render_the_kaomoji_as_multi_cell_ascii_for_every_species() {
        let config = Config::sample().unwrap();
        let ramp: Vec<char> = config.voxel_lighting.ascii_ramp.chars().collect();
        let mut species_shaded = [0usize; 16];
        for shape in 0..16u8 {
            for stage in [Stage::Child, Stage::Adult] {
                let appearance = Appearance {
                    shape,
                    body: shape,
                    accent: (shape + 5) % 16,
                };
                let front = Sprite::voxel_with_appearance(
                    stage,
                    Expression::Happy,
                    SpriteStyle::SixteenBit,
                    0,
                    0,
                    false,
                    appearance,
                );
                let rear = Sprite::voxel_with_appearance(
                    stage,
                    Expression::Happy,
                    SpriteStyle::SixteenBit,
                    0,
                    180,
                    false,
                    appearance,
                );
                let texture = FaceTexture::new(&front).unwrap();
                let face = FaceTexture::bounds(&front).unwrap();
                for renderer in [Renderer::Ascii, Renderer::HalfBlock] {
                    for depth in [Depth::Mono, Depth::Rgb] {
                        let colors = Colors::from_config(&config, depth).unwrap();
                        for (width, height) in [(40, 12), (56, 16), (64, 20), (80, 24)] {
                            for zoom_percent in [100, 150, 200, 250, 300] {
                                let label = format!(
                                    "shape {shape} {stage:?} {renderer:?} {depth:?} {width}x{height}@{zoom_percent}"
                                );
                                let area = Rect::new(0, 0, width, height);
                                let camera = Camera {
                                    view: CameraView::Face,
                                    from_view: CameraView::Face,
                                    zoom_percent,
                                    ..Camera::default()
                                };
                                let projection = Projection::camera(
                                    &front,
                                    SolidBounds::of(&front).unwrap(),
                                    area,
                                    renderer,
                                    0,
                                    (500, 500),
                                    camera,
                                );
                                let (left, top) = projection.cell(face.left, face.top, renderer);
                                let (right, bottom) =
                                    projection.cell(face.right, face.bottom, renderer);
                                let pixel_atlas = renderer.packing() != (1, 1);
                                if face_lod(texture, right - left, bottom - top, pixel_atlas)
                                    != FaceLod::Shaded
                                {
                                    continue;
                                }
                                let paint = |sprite: &Sprite| {
                                    let mut buf = Buffer::empty(area);
                                    draw_projection(
                                        sprite, renderer, &colors, projection, area, &mut buf,
                                    );
                                    let body = buf.clone();
                                    draw_camera_face(
                                        sprite, renderer, &colors, projection, area, &mut buf,
                                    );
                                    (body, buf)
                                };
                                let (rear_body, rear_face) = paint(&rear);
                                assert!(
                                    rear_body.content.iter().zip(&rear_face.content).all(
                                        |(a, b)| {
                                            a == b
                                                || !ramp.contains(
                                                    &b.symbol().chars().next().unwrap_or(' '),
                                                )
                                        }
                                    ),
                                    "{label}: the rear view gained face strokes"
                                );
                                let (body, after) = paint(&front);
                                let centre = (left + right) / 2.0;
                                let (mut strokes, mut left_half, mut right_half) =
                                    (0, false, false);
                                let (dx, dy) = renderer.packing();
                                for y in 0..height {
                                    for x in 0..width {
                                        if body[(x, y)] == after[(x, y)] {
                                            continue;
                                        }
                                        let cell = &after[(x, y)];
                                        let glyph = cell.symbol().chars().next().unwrap();
                                        assert!(
                                            ramp.contains(&glyph),
                                            "{label}: {glyph:?} at {x},{y} is not shading"
                                        );
                                        assert_eq!(
                                            cell.fg,
                                            face_ink(&front, &colors, InkRegion::TextBox),
                                            "{label}: stroke ink at {x},{y}"
                                        );
                                        for sy in 0..dy {
                                            for sx in 0..dx {
                                                let px =
                                                    u32::from(x) * u32::from(dx) + u32::from(sx);
                                                let py =
                                                    u32::from(y) * u32::from(dy) + u32::from(sy);
                                                assert!(
                                                    projected_face_uv(&front, projection, px, py).is_some(),
                                                    "{label}: stroke at {x},{y} leaves the face plane"
                                                );
                                            }
                                        }
                                        strokes += 1;
                                        left_half |= f64::from(x) + 0.5 < centre;
                                        right_half |= f64::from(x) + 0.5 > centre;
                                    }
                                }
                                assert!(
                                    strokes >= texture.text.len(),
                                    "{label}: only {strokes} shaded cells"
                                );
                                assert!(
                                    left_half && right_half,
                                    "{label}: both eye halves need strokes"
                                );
                                species_shaded[usize::from(shape)] += 1;
                            }
                        }
                    }
                }
            }
        }
        assert!(
            species_shaded.iter().all(|n| *n > 0),
            "every species must reach the shaded tier: {species_shaded:?}"
        );
    }

    #[test]
    fn moderate_heads_draw_one_connected_kaomoji_word_on_real_head_cells() {
        let config = Config::sample().unwrap();
        let mut connected = 0;
        for shape in 0..16 {
            for stage in [Stage::Child, Stage::Adult] {
                let appearance = Appearance {
                    shape,
                    body: shape,
                    accent: (shape + 5) % 16,
                };
                let front = Sprite::voxel_with_appearance(
                    stage,
                    Expression::Happy,
                    SpriteStyle::SixteenBit,
                    0,
                    0,
                    false,
                    appearance,
                );
                let rear = Sprite::voxel_with_appearance(
                    stage,
                    Expression::Happy,
                    SpriteStyle::SixteenBit,
                    0,
                    180,
                    false,
                    appearance,
                );
                let text = front.face_text().unwrap();
                for renderer in [Renderer::Ascii, Renderer::HalfBlock] {
                    for depth in [Depth::Mono, Depth::Rgb] {
                        let colors = Colors::from_config(&config, depth).unwrap();
                        for (width, height) in [(24, 6), (32, 8), (40, 10)] {
                            let label = format!(
                                "shape {shape} {stage:?} {renderer:?} {depth:?} {width}x{height}"
                            );
                            let area = Rect::new(0, 0, width, height);
                            let camera = Camera {
                                view: CameraView::Face,
                                from_view: CameraView::Face,
                                ..Camera::default()
                            };
                            let rows = |sprite: &Sprite| {
                                let mut buf = Buffer::empty(area);
                                let projection = Projection::camera(
                                    sprite,
                                    SolidBounds::of(sprite).unwrap(),
                                    area,
                                    renderer,
                                    0,
                                    (500, 500),
                                    camera,
                                );
                                draw_projection(
                                    sprite, renderer, &colors, projection, area, &mut buf,
                                );
                                // The native face path alone: no caption can satisfy this.
                                let painted = draw_surface_glyphs(
                                    sprite, renderer, &colors, projection, area, &mut buf,
                                );
                                let rows: Vec<String> = (0..height)
                                    .map(|y| (0..width).map(|x| buf[(x, y)].symbol()).collect())
                                    .collect();
                                (painted, rows, projection)
                            };
                            let (_, rear_rows, _) = rows(&rear);
                            assert!(
                                rear_rows.iter().all(|line| !line.contains(text)),
                                "{label}: rear view revealed the face"
                            );
                            let (painted, front_rows, projection) = rows(&front);
                            if !painted {
                                continue;
                            }
                            let (row, line) = front_rows
                                .iter()
                                .enumerate()
                                .find(|(_, line)| line.contains(text))
                                .unwrap_or_else(|| {
                                    panic!("{label}: expression not one connected word: {front_rows:#?}")
                                });
                            connected += 1;
                            let column = line[..line.find(text).unwrap()].chars().count();
                            let (dx, dy) = renderer.packing();
                            for k in 0..text.len() {
                                for sy in 0..dy {
                                    for sx in 0..dx {
                                        let px =
                                            (column + k) as u32 * u32::from(dx) + u32::from(sx);
                                        let py = row as u32 * u32::from(dy) + u32::from(sy);
                                        assert!(
                                            projected_head_uv(&front, projection, px, py).is_some(),
                                            "{label}: glyph {k} is not on visible head material"
                                        );
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        assert!(connected >= 32, "only {connected} connected native faces");
    }
}
