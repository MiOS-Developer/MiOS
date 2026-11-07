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

impl CameraView {
    /// Weight order of a per-mille view mix: Room, Pet, Face.
    pub const ALL: [CameraView; 3] = [CameraView::Room, CameraView::Pet, CameraView::Face];

    pub fn index(self) -> usize {
        match self {
            CameraView::Room => 0,
            CameraView::Pet => 1,
            CameraView::Face => 2,
        }
    }

    /// This view alone, as a per-mille mix.
    pub fn one_hot(self) -> [u16; 3] {
        let mut weights = [0; 3];
        weights[self.index()] = 1000;
        weights
    }

    /// The heaviest view of a mix; ties resolve to the earlier view.
    pub fn dominant(weights: [u16; 3]) -> Self {
        let mut best = CameraView::Room;
        for view in Self::ALL {
            if weights[view.index()] > weights[best.index()] {
                best = view;
            }
        }
        best
    }

    /// The mix drawn `percent` of the way from `source` to this view. The
    /// rounding remainder belongs to this view, so the weights sum to 1000.
    pub fn blend_from(self, source: [u16; 3], percent: u16) -> [u16; 3] {
        let percent = i32::from(percent.min(100));
        let target = self.one_hot();
        let mut weights = [0u16; 3];
        for (index, weight) in weights.iter_mut().enumerate() {
            let from = i32::from(source[index].min(1000));
            let to = i32::from(target[index]);
            *weight = (from + (to - from) * percent / 100).clamp(0, 1000) as u16;
        }
        let sum: i32 = weights.iter().map(|&w| i32::from(w)).sum();
        let own = &mut weights[self.index()];
        *own = (i32::from(*own) + 1000 - sum).clamp(0, 1000) as u16;
        weights
    }
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
    /// Per-mille Room/Pet/Face framing mix the transition starts from. None
    /// means `from_view` alone, so an interrupted blend never jumps.
    pub from_weights: Option<[u16; 3]>,
    pub pan_permille: (i16, i16),
    pub elevation_radians: Option<f32>,
}

impl Default for Camera {
    fn default() -> Self {
        Self {
            orbit_degrees: 45,
            view: CameraView::Pet,
            zoom_percent: 100,
            from_view: CameraView::Pet,
            transition_percent: 100,
            from_weights: None,
            pan_permille: (0, 0),
            elevation_radians: None,
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
        // A one-view source is used directly, keeping one-hot results exact.
        let from = match camera.from_weights {
            Some(weights) if weights.iter().filter(|&&w| w > 0).count() > 1 => {
                let total = f64::from(weights.iter().map(|&w| u32::from(w)).sum::<u32>());
                let mut mixed = Self {
                    left: 0.0,
                    top: 0.0,
                    scale: 0.0,
                    scale_y: 0.0,
                };
                for view in CameraView::ALL {
                    let weight = weights[view.index()];
                    if weight == 0 {
                        continue;
                    }
                    let share = f64::from(weight) / total;
                    let framed = mode(view);
                    mixed.left += framed.left * share;
                    mixed.top += framed.top * share;
                    mixed.scale += framed.scale * share;
                    mixed.scale_y += framed.scale_y * share;
                }
                mixed
            }
            Some(weights) if weights.iter().any(|&w| w > 0) => mode(CameraView::dominant(weights)),
            _ => mode(camera.from_view),
        };
        let to = mode(camera.view);
        let blend = f64::from(camera.transition_percent.min(100)) / 100.0;
        Self {
            left: from.left + (to.left - from.left) * blend
                - f64::from(area.width)
                    * f64::from(renderer.packing().0)
                    * f64::from(camera.pan_permille.0.clamp(-400, 400))
                    / 1000.0,
            top: from.top + (to.top - from.top) * blend
                - f64::from(area.height)
                    * f64::from(renderer.packing().1)
                    * f64::from(camera.pan_permille.1.clamp(-400, 400))
                    / 1000.0,
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
    /// The voxel the sample shows and its screen neighbourhood. None (world
    /// samples, legacy pixel sprites) keeps the exact legacy lit packing.
    overlay: Option<OverlaySample>,
}

/// Screen-neighbour bits of one packed sample: the four sides, then the four
/// diagonals (diagonals only ever set `outside`).
const DIR_N: u8 = 1;
const DIR_E: u8 = 2;
const DIR_S: u8 = 4;
const DIR_W: u8 = 8;
const DIR_NE: u8 = 16;
const DIR_SE: u8 = 32;
const DIR_SW: u8 = 64;
const DIR_NW: u8 = 128;
const NEIGHBOUR_STEPS: [(i32, i32, u8); 8] = [
    (0, -1, DIR_N),
    (1, 0, DIR_E),
    (0, 1, DIR_S),
    (-1, 0, DIR_W),
    (1, -1, DIR_NE),
    (1, 1, DIR_SE),
    (-1, 1, DIR_SW),
    (-1, -1, DIR_NW),
];

/// The voxel ASCII overlay's view of one packed screen sample. Every field is
/// a function of the model (lattice voxel, exposed face, material, depth) and
/// of which model surfaces the neighbouring screen samples show, so glyphs
/// stay attached to voxels through orbit, zoom, occlusion and resampling.
#[derive(Clone, Copy, Debug, PartialEq)]
struct OverlaySample {
    voxel: [i32; 3],
    /// Quantized view normal of the exposed cube face: (voxel, facet) is one
    /// facet, compared exactly.
    facet: [i8; 3],
    material: u8,
    /// Source-pixel camera depth, larger is nearer.
    depth: f32,
    /// Packed screen pixels per lattice cell along x and y.
    voxel_span: [f32; 2],
    /// Neighbour shows no Mon (all eight directions).
    outside: u8,
    /// Neighbour is another, farther part of the Mon (not lattice-adjacent):
    /// this sample is the near side of a contour.
    contour: u8,
    /// As `contour`, with a depth jump of at least three voxels.
    deep_contour: u8,
    /// Lattice-adjacent neighbour on a differently oriented face.
    crease: u8,
    /// Lattice-adjacent neighbour on the same orientation, other material.
    seam: u8,
    /// The N/E/S/W neighbour's facet normal and material.
    neighbours: [([i8; 3], u8); 4],
    /// The face plane within the clearance of the kaomoji as fitted to the
    /// view: the overlay's quiet zone. (Native characters clear the shading
    /// beside them when they are drawn.)
    face_plane: bool,
}

/// The kaomoji texture and its fit to the viewport, which bound the quiet
/// zone; without one the whole face plane is quiet.
type FaceClearance = Option<(FaceTexture, FaceFit)>;

/// Clearance kept around kaomoji ink on the face plane, in glyph-grid units.
const FACE_HALO_GRID: f64 = 2.5;

/// The same texture and fit `draw_camera_face` lays the kaomoji with.
fn face_clearance(
    sprite: &Sprite,
    renderer: Renderer,
    projection: Projection,
    area: Rect,
) -> FaceClearance {
    let texture = FaceTexture::new(sprite)?;
    let bounds = FaceTexture::bounds(sprite)?;
    let (left, top) = projection.cell(bounds.left, bounds.top, renderer);
    let (right, bottom) = projection.cell(bounds.right, bounds.bottom, renderer);
    let fit = FaceFit::of(
        sprite,
        renderer,
        projection,
        area,
        texture,
        [left, top, right, bottom],
    );
    Some((texture, fit))
}

/// A face-plane sample within the halo of the fitted kaomoji's ink.
fn near_kaomoji(
    sprite: &Sprite,
    projection: Projection,
    (x, y): (u32, u32),
    face: FaceClearance,
) -> bool {
    let Some((texture, fit)) = face else {
        return true;
    };
    let Some((uv, footprint)) = projected_face_uv(sprite, projection, x, y) else {
        return true;
    };
    let reach = texture.glyph_reach();
    let halo = [
        2.0 * FACE_HALO_GRID * reach[0] / 8.0,
        2.0 * FACE_HALO_GRID * reach[1] / 11.0,
    ];
    let footprint = fit.footprint(footprint);
    texture.coverage(
        fit.map(uv),
        [footprint[0] + halo[0], footprint[1] + halo[1]],
        f64::INFINITY,
    ) > 0.0
}

fn overlay_sample(
    sprite: &Sprite,
    projection: Projection,
    (x, y): (u32, u32),
    (sx, sy): (u32, u32),
    face: FaceClearance,
) -> Option<OverlaySample> {
    let (voxel, facet) = sprite.surface_facet(sx, sy)?;
    let extent = sprite.voxel_extent_pixels()?;
    let material = sprite.pixel(sx, sy, 1)?;
    let depth = sprite.depth_pixels(sx, sy)?;
    let span = [projection.scale * extent, projection.scale_y * extent];
    // Lattice cells one packed step may cross on one continuous surface: a
    // minified camera skips voxels between neighbouring samples.
    let tolerance = span.map(|s| 1 + (1.0 / s.max(1.0e-3)).ceil() as i32);
    let mut sample = OverlaySample {
        voxel,
        facet,
        material,
        depth: depth as f32,
        voxel_span: span.map(|s| s as f32),
        outside: 0,
        contour: 0,
        deep_contour: 0,
        crease: 0,
        seam: 0,
        neighbours: [(facet, material); 4],
        face_plane: valid_face_uv(sprite, sx, sy).is_some()
            && near_kaomoji(sprite, projection, (x, y), face),
    };
    for (side, (ox, oy, bit)) in NEIGHBOUR_STEPS.into_iter().enumerate() {
        // Screen neighbours before the buffer origin are cropped, not empty.
        let (Some(nx), Some(ny)) = (x.checked_add_signed(ox), y.checked_add_signed(oy)) else {
            continue;
        };
        let Some((nsx, nsy)) = projection.source_pixel(sprite, nx, ny) else {
            sample.outside |= bit;
            continue;
        };
        let Some(other_material) = sprite.pixel(nsx, nsy, 1) else {
            sample.outside |= bit;
            continue;
        };
        if side >= 4 || (nsx, nsy) == (sx, sy) {
            continue;
        }
        let Some((other_voxel, other_facet)) = sprite.surface_facet(nsx, nsy) else {
            continue;
        };
        sample.neighbours[side] = (other_facet, other_material);
        let axis = usize::from(ox == 0);
        let lattice = (0..3)
            .map(|i| (voxel[i] - other_voxel[i]).abs())
            .max()
            .unwrap_or(0);
        if lattice > tolerance[axis] {
            // Another part of the model: the near side carries the line; at
            // equal depth the greater lattice key does, so it is drawn once.
            let jump = depth - sprite.depth_pixels(nsx, nsy).unwrap_or(depth);
            if jump > 0.0 || (jump == 0.0 && voxel > other_voxel) {
                sample.contour |= bit;
                if jump >= 3.0 * extent {
                    sample.deep_contour |= bit;
                }
            }
        } else if other_facet != facet {
            sample.crease |= bit;
        } else if other_material != material {
            sample.seam |= bit;
        }
    }
    Some(sample)
}

fn projected_surface_light(
    sprite: &Sprite,
    projection: Projection,
    (x, y): (u32, u32),
    face: FaceClearance,
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
    Some(SurfaceLight {
        normal,
        occlusion,
        edge: edge || screen_edge,
        screen_edge,
        overlay: overlay_sample(sprite, projection, (x, y), (sx, sy), face),
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

/// Line glyphs draw outlines, contours and creases; they never shade.
const LINE_GLYPHS: &[u8] = b"-_|/\\()^v<>";
/// Crease and seam lines fade in, voxel by voxel, over this cells-per-voxel band.
const CREASE_BAND: (f32, f32) = (0.8, 1.6);
/// The lattice checker fades in over this cells-per-voxel band.
const CHECKER_BAND: (f32, f32) = (2.5, 4.0);
/// Shallow contours fade in, voxel by voxel, over this cells-per-voxel band;
/// below it a contour needs a three-voxel depth jump.
const MINIFIED_CONTOUR_BAND: (f32, f32) = (0.35, 0.65);

/// The configured ramp minus the line alphabet, dim to bright, so a shading
/// glyph is never mistaken for an outline. A ramp left with fewer than two
/// glyphs is used unchanged.
fn shading_ramp(lighting: &crate::lighting::VoxelLighting) -> ([u8; 16], usize) {
    let mut ramp = [0u8; 16];
    let mut len = 0;
    let mut fill = |keep: &dyn Fn(u8) -> bool| {
        len = 0;
        for byte in lighting.ascii_ramp.bytes().filter(|b| keep(*b)) {
            if len < ramp.len() {
                ramp[len] = byte;
                len += 1;
            }
        }
        len
    };
    if fill(&|b| b.is_ascii_graphic() && !LINE_GLYPHS.contains(&b)) < 2 {
        fill(&|b| b.is_ascii_graphic());
    }
    (ramp, len)
}

/// Brightness normalized over the configured light range, never the frame's:
/// fully occluded ambient is 0, ambient plus full diffuse is 1.
fn light_level(lighting: &crate::lighting::VoxelLighting, brightness: u16) -> f32 {
    let ambient = f32::from(lighting.ambient_percent.clamp(20, 100));
    let low = ambient * (1.0 - f32::from(lighting.occlusion_percent.min(100)) / 100.0);
    let high = (ambient + f32::from(lighting.diffuse_percent.min(100))).min(180.0);
    ((f32::from(brightness) - low) / (high - low).max(1.0)).clamp(0.0, 1.0)
}

/// A stable per-voxel fraction in [0, 1) for band dithers.
fn voxel_hash(voxel: [i32; 3]) -> f32 {
    let mut h = (voxel[0] as u32).wrapping_mul(0x9E37_79B1)
        ^ (voxel[1] as u32).wrapping_mul(0x85EB_CA77)
        ^ (voxel[2] as u32).wrapping_mul(0xC2B2_AE3D);
    h ^= h >> 15;
    h = h.wrapping_mul(0x2C1B_3C6D);
    h ^= h >> 12;
    (h >> 8) as f32 / (1u32 << 24) as f32
}

fn smoothstep((low, high): (f32, f32), x: f32) -> f32 {
    let t = ((x - low) / (high - low)).clamp(0.0, 1.0);
    t * t * (3.0 - 2.0 * t)
}

fn separation(a: Color, b: Color) -> u32 {
    match (luminance(a), luminance(b)) {
        (Some(a), Some(b)) => a.abs_diff(b),
        _ => 0,
    }
}

/// Minimum luma separation of shading, crease and contour ink from its voxel.
fn hatch_separation(lighting: &crate::lighting::VoxelLighting) -> u32 {
    (outline_separation(lighting) / 3).max(12)
}

/// The first candidate separated from `bg` by `minimum`, else the most
/// separated one (a 16-colour console may offer no adequate ink).
fn legible_ink(bg: Color, minimum: u32, candidates: impl Iterator<Item = Color> + Clone) -> Color {
    candidates
        .clone()
        .find(|color| *color != bg && separation(*color, bg) >= minimum)
        .or_else(|| candidates.max_by_key(|color| separation(*color, bg)))
        .unwrap_or(bg)
}

/// Silhouette ink, dark first: outline ink, then the material's darkest and
/// brightest rims, then the palette.
fn outline_ink(
    lighting: &crate::lighting::VoxelLighting,
    colors: &Colors,
    base: Color,
    against: Color,
) -> Color {
    let rims = [
        colors.ink(0),
        shade_terminal_color(lighting, base, 0),
        shade_terminal_color(lighting, base, 180),
    ];
    legible_ink(
        against,
        outline_separation(lighting),
        rims.into_iter().chain(colors.palette.iter().copied()),
    )
}

/// The material in its own shadow (contours and creases) or half-shadow
/// (hatching), brightened instead on a surface too dark to darken.
fn tint_ink(
    lighting: &crate::lighting::VoxelLighting,
    colors: &Colors,
    (base, brightness): (Color, u16),
    percent: u16,
    bg: Color,
) -> Color {
    let lit = shade_terminal_color(lighting, base, brightness);
    // A material too dark to darken or brighten visibly is lightened toward
    // white, keeping its hue family, before any other palette colour.
    let tints = [
        shade_terminal_color(lighting, base, brightness * percent / 100),
        shade_terminal_color(lighting, base, (brightness * 3 / 2).min(180)),
        lighten(lit, 35),
        lighten(lit, 60),
    ];
    legible_ink(
        bg,
        hatch_separation(lighting),
        tints.into_iter().chain(colors.palette.iter().copied()),
    )
}

/// D-table: the side mask (N, E, S, W) of a boundary to its line glyph. A
/// single vertical side bends with the diagonals [NE, SE, SW, NW] that still
/// show the Mon. Kaomoji eye glyphs (o, O, +, *) are never outlines.
fn outline_glyph(sides: u8, diagonal_mon: [bool; 4]) -> Option<char> {
    let [ne, se, sw, nw] = diagonal_mon;
    Some(match sides & 15 {
        1 | 5 => '-',
        4 => '_',
        2 if ne && !se => '/',
        2 if se && !ne => '\\',
        8 if nw && !sw => '\\',
        8 if sw && !nw => '/',
        2 | 8 | 10 => '|',
        3 | 12 => '\\',
        6 | 9 => '/',
        11 => '^',
        14 => 'v',
        7 => '>',
        13 => '<',
        15 => '.',
        _ => return None,
    })
}

/// A boundary inside one packed cell runs between its samples: horizontal,
/// vertical or diagonal by the centroids of the marked and unmarked samples.
/// Boundaries on the cell border use the D-table.
fn boundary_glyph(
    marks: &[u8; 8],
    mon: &[Option<OverlaySample>; 8],
    (dx, dy): (usize, usize),
) -> Option<char> {
    let count = dx * dy;
    let (mut inner, mut border) = (0u8, 0u8);
    for (i, mark) in marks.iter().enumerate().take(count) {
        let (x, y) = (i % dx, i / dx);
        for (bit, inside) in [
            (DIR_N, y > 0),
            (DIR_E, x + 1 < dx),
            (DIR_S, y + 1 < dy),
            (DIR_W, x > 0),
        ] {
            if mark & bit != 0 {
                if inside {
                    inner |= bit;
                } else {
                    border |= bit;
                }
            }
        }
    }
    let vertical = inner & (DIR_N | DIR_S) != 0;
    let horizontal = inner & (DIR_E | DIR_W) != 0;
    match (vertical, horizontal) {
        (true, false) => Some('-'),
        (false, true) => Some('|'),
        (true, true) => {
            let centroid = |marked: bool| {
                let (mut sum, mut n) = ([0.0f32; 2], 0.0f32);
                for i in 0..count {
                    if mon[i].is_some() && (marks[i] != 0) == marked {
                        sum[0] += (i % dx) as f32;
                        sum[1] += (i / dx) as f32;
                        n += 1.0;
                    }
                }
                [sum[0] / n.max(1.0), sum[1] / n.max(1.0)]
            };
            let (a, b) = (centroid(true), centroid(false));
            Some(if (b[0] - a[0]) * (b[1] - a[1]) >= 0.0 {
                '/'
            } else {
                '\\'
            })
        }
        (false, false) => outline_glyph(border, [false; 4]),
    }
}

/// Partial cells trade the block for an ASCII glyph only when the glyph's
/// nominal ink re-expresses the Mon samples' mask (exact for half-block and
/// quadrant, within one sample for braille, at most half the cell).
fn partial_glyph(renderer: Renderer, mask: u8, mon: &[Option<OverlaySample>; 8]) -> Option<char> {
    let isolated = |left: usize, right: usize| {
        mon[left].is_some_and(|o| o.outside & DIR_W != 0)
            && mon[right].is_some_and(|o| o.outside & DIR_E != 0)
    };
    match renderer {
        Renderer::HalfBlock => match mask {
            2 if isolated(1, 1) => Some('^'),
            2 => Some('_'),
            _ => None,
        },
        Renderer::Quadrant => match mask {
            1 => Some('`'),
            2 => Some('\''),
            4 => Some(','),
            8 => Some('.'),
            12 => Some('_'),
            5 | 10 => Some('|'),
            9 => Some('\\'),
            6 => Some('/'),
            _ => None,
        },
        Renderer::Braille => {
            if mask.count_ones() > 4 {
                return None;
            }
            const NOMINAL: [(char, u8); 14] = [
                ('_', 0xC0),
                ('.', 0x40),
                ('.', 0x80),
                ('-', 0x0C),
                ('-', 0x30),
                ('|', 0x55),
                ('|', 0xAA),
                ('/', 0x5A),
                ('\\', 0xA5),
                ('(', 0x96),
                (')', 0x69),
                ('`', 0x01),
                ('\'', 0x02),
                ('^', 0x03),
            ];
            let mut best: Option<(u32, char)> = None;
            for (glyph, nominal) in NOMINAL {
                if glyph == '^' && !isolated(0, 1) {
                    continue;
                }
                let distance = (nominal ^ mask).count_ones();
                if distance <= 1 && best.is_none_or(|(d, _)| distance < d) {
                    best = Some((distance, glyph));
                }
            }
            best.map(|(_, glyph)| glyph)
        }
        Renderer::Auto | Renderer::Ascii => None,
    }
}

fn block_glyph(renderer: Renderer, mask: u8) -> char {
    let bits: [bool; 8] = std::array::from_fn(|i| mask & (1 << i) != 0);
    match renderer {
        Renderer::HalfBlock => match mask {
            1 => '▀',
            2 => '▄',
            _ => '█',
        },
        Renderer::Quadrant => quadrant(std::array::from_fn(|i| bits[i])),
        Renderer::Braille => braille(bits),
        Renderer::Auto | Renderer::Ascii => '#',
    }
}

/// The voxel ASCII overlay for one terminal cell, in class order: partial
/// silhouette, rim outline, contour, quiet face zone, crease or seam, then
/// interior shading. Returns false when the cell keeps the legacy lit
/// packing: no identity-bearing Mon sample, or the quiet face zone in
/// half-block and monochrome (face strokes and the atlas rely on that
/// packing; quadrant and braille keep it over the voxel's lit colour).
fn paint_overlay_cell(
    style: (Renderer, &Colors, &crate::lighting::VoxelLighting),
    pixels: [Option<u8>; 8],
    surface: [Option<SurfaceLight>; 8],
    scene: Color,
    cell: &mut ratatui::buffer::Cell,
) -> bool {
    let (renderer, colors, lighting) = style;
    let (dx, dy) = renderer.packing();
    let (dx, dy) = (usize::from(dx), usize::from(dy));
    let count = dx * dy;
    let mono = colors.palette.iter().all(|c| *c == Color::Reset);
    let mon: [Option<OverlaySample>; 8] = std::array::from_fn(|i| {
        if i < count && pixels[i].is_some() {
            surface[i].and_then(|s| s.overlay)
        } else {
            None
        }
    });
    let Some(first) = mon.iter().flatten().next().copied() else {
        return false;
    };
    let base = |i: usize| colors.ink(usize::from(pixels[i].unwrap_or(0)));
    // Outline darkening is not applied: the outline glyph replaces it.
    let brightness: [u16; 8] = std::array::from_fn(|i| {
        surface[i].map_or(100, |s| lighting.brightness(s.normal, s.occlusion, false))
    });
    let lit: [Color; 8] =
        std::array::from_fn(|i| shade_terminal_color(lighting, base(i), brightness[i]));
    // The scene behind a mixed cell is the world surface sharing it (the
    // floor or wall behind the pet), its most frequent lit colour.
    let mut scene = scene;
    let mut best = 0;
    for i in 0..count {
        if pixels[i].is_none() || mon[i].is_some() {
            continue;
        }
        let world = |j: usize| {
            surface[j].map_or(base(j), |s| {
                shade_terminal_color(
                    lighting,
                    base(j),
                    lighting.brightness(s.normal, s.occlusion, s.edge),
                )
            })
        };
        let colour = world(i);
        let n = (0..count)
            .filter(|&j| pixels[j].is_some() && mon[j].is_none() && world(j) == colour)
            .count();
        if n > best {
            best = n;
            scene = colour;
        }
    }
    let mask = (0..count)
        .filter(|&i| mon[i].is_some())
        .fold(0u8, |mask, i| mask | (1 << i));
    // Dominant facet: most samples, then nearer, then brighter.
    let dominant = (0..count)
        .filter(|&i| mon[i].is_some())
        .max_by(|&a, &b| {
            let n = |i: usize| {
                (0..count)
                    .filter(|&j| mon[j].is_some() && lit[j] == lit[i])
                    .count()
            };
            let depth = |i: usize| mon[i].map_or(f32::MIN, |o| o.depth);
            n(a).cmp(&n(b))
                .then(depth(a).total_cmp(&depth(b)))
                .then(brightness[a].cmp(&brightness[b]))
                .then(b.cmp(&a))
        })
        .unwrap_or(0);
    let cells_per_voxel = (first.voxel_span[0] / dx as f32).min(first.voxel_span[1] / dy as f32);
    let mut paint = |glyph: char, fg: Color, bg: Color| {
        cell.set_char(glyph);
        if !mono {
            cell.set_fg(fg).set_bg(bg);
        }
    };

    // Partial: the silhouette crosses the cell. The scene stays its
    // background; the Mon's samples are drawn in outline ink.
    if (mask.count_ones() as usize) < count {
        let glyph =
            partial_glyph(renderer, mask, &mon).unwrap_or_else(|| block_glyph(renderer, mask));
        paint(
            glyph,
            outline_ink(lighting, colors, base(dominant), scene),
            scene,
        );
        return true;
    }

    // Rim: the silhouette runs along a whole side of the cell.
    let on_side = |i: usize, bit: u8| match bit {
        DIR_N => i / dx == 0,
        DIR_E => i % dx == dx - 1,
        DIR_S => i / dx == dy - 1,
        _ => i.is_multiple_of(dx),
    };
    let mut rim = 0u8;
    for bit in [DIR_N, DIR_E, DIR_S, DIR_W] {
        if (0..count)
            .filter(|&i| on_side(i, bit))
            .all(|i| mon[i].is_some_and(|o| o.outside & bit != 0))
        {
            rim |= bit;
        }
    }
    let shows_mon = |i: usize, bit: u8| mon[i].is_some_and(|o| o.outside & bit == 0);
    let corners = [
        shows_mon(dx - 1, DIR_NE),
        shows_mon(count - 1, DIR_SE),
        shows_mon(count - dx, DIR_SW),
        shows_mon(0, DIR_NW),
    ];
    if let Some(glyph) = outline_glyph(rim, corners) {
        let bg = lit[dominant];
        paint(glyph, outline_ink(lighting, colors, base(dominant), bg), bg);
        return true;
    }

    // Contour: a nearer part over a farther one (paw over body, ear over
    // head, nose over face). Never drawn on the face plane itself.
    // Minified, only deep contours remain; across the band each voxel gains
    // its shallow contours at its own share, so a zoom never drops or adds
    // every shallow contour at once.
    let shallow = smoothstep(MINIFIED_CONTOUR_BAND, cells_per_voxel);
    let contours: [u8; 8] = std::array::from_fn(|i| {
        mon[i].filter(|o| !o.face_plane).map_or(0, |o| {
            if voxel_hash(o.voxel) < shallow {
                o.contour
            } else {
                o.deep_contour
            }
        })
    });
    if let Some(near) = (0..count).filter(|&i| contours[i] != 0).max_by(|&a, &b| {
        let depth = |i: usize| mon[i].map_or(f32::MIN, |o| o.depth);
        depth(a).total_cmp(&depth(b))
    }) {
        if let Some(glyph) = boundary_glyph(&contours, &mon, (dx, dy)) {
            let bg = lit[near];
            paint(
                glyph,
                tint_ink(lighting, colors, (base(near), brightness[near]), 45, bg),
                bg,
            );
            return true;
        }
    }

    // Quiet zone: the kaomoji's face plane carries no shading, crease or
    // seam mark. Quadrant and braille keep their full block pattern over the
    // voxel's own lit colour, so the room never shows between braille dots
    // on the face; half-block keeps its two lit colour planes.
    if (0..count).any(|i| mon[i].is_some_and(|o| o.face_plane)) {
        if mono || renderer == Renderer::HalfBlock {
            return false;
        }
        let glyph = if count == 1 {
            ' '
        } else {
            block_glyph(renderer, mask)
        };
        paint(glyph, lit[dominant], lit[dominant]);
        return true;
    }

    // Crease (orientation change) on the darker face; seam (material change)
    // on the lighter material, in the darker material's colour. Both fade in
    // voxel by voxel across the crease band.
    let band = smoothstep(CREASE_BAND, cells_per_voxel);
    let mut lines = [0u8; 8];
    let mut ink: Option<Color> = None;
    for i in 0..count {
        let Some(o) = mon[i] else {
            continue;
        };
        if voxel_hash(o.voxel) >= band {
            continue;
        }
        for side in 0..4 {
            let bit = 1u8 << side;
            let (facet, material) = o.neighbours[side];
            if o.crease & bit != 0 {
                let light =
                    |n: [i8; 3]| lighting.brightness(n.map(|v| f32::from(v) / 127.0), 0, false);
                let (own, other) = (light(o.facet), light(facet));
                if own < other || (own == other && o.facet < facet) {
                    lines[i] |= bit;
                    ink.get_or_insert_with(|| {
                        tint_ink(
                            lighting,
                            colors,
                            (base(i), brightness[i]),
                            45,
                            lit[dominant],
                        )
                    });
                }
            } else if o.seam & bit != 0 {
                let luma = |m: u8| luminance(colors.ink(usize::from(m)));
                let (own, other) = (luma(o.material), luma(material));
                if own > other || (own == other && o.material > material) {
                    lines[i] |= bit;
                    ink.get_or_insert_with(|| {
                        let darker = shade_terminal_color(
                            lighting,
                            colors.ink(usize::from(material)),
                            brightness[i],
                        );
                        if separation(darker, lit[dominant]) >= hatch_separation(lighting) {
                            darker
                        } else {
                            tint_ink(
                                lighting,
                                colors,
                                (base(i), brightness[i]),
                                45,
                                lit[dominant],
                            )
                        }
                    });
                }
            }
        }
    }
    if let (Some(glyph), Some(fg)) = (boundary_glyph(&lines, &mon, (dx, dy)), ink) {
        paint(glyph, fg, lit[dominant]);
        return true;
    }

    // Interior: the filtered ramp over the full configured light range.
    // Colour hatches (denser is darker) over the lit voxel; mono has only
    // glyphs (denser is brighter). Magnified voxels alternate neighbouring
    // ramp glyphs by lattice parity, so the checker turns with the model.
    let (ramp, len) = shading_ramp(lighting);
    if len == 0 {
        return false;
    }
    let level = (0..count)
        .filter_map(|i| mon[i].map(|_| light_level(lighting, brightness[i])))
        .sum::<f32>()
        / mask.count_ones() as f32;
    // The checker follows the cell's least lattice key, a function of the
    // voxels shown alone (never of which sample happens to be nearer).
    let voxel = mon
        .iter()
        .flatten()
        .map(|o| o.voxel)
        .min()
        .unwrap_or(first.voxel);
    // Odd lattice voxels step one glyph lighter (colour) or dimmer (mono)
    // within their band, so the checker never moves a band boundary.
    let checker = voxel_hash(voxel) < smoothstep(CHECKER_BAND, cells_per_voxel)
        && (voxel[0] + voxel[1] + voxel[2]).rem_euclid(2) == 1;
    let tone = if mono { level } else { 1.0 - level };
    let band = (tone * (len - 1) as f32)
        .round()
        .clamp(0.0, (len - 1) as f32) as usize;
    let index = if checker {
        band.saturating_sub(1)
    } else {
        band
    };
    let bg = lit[dominant];
    let other = (0..count)
        .filter(|&i| mon[i].is_some() && lit[i] != bg)
        .map(|i| lit[i])
        .find(|colour| separation(*colour, bg) >= hatch_separation(lighting));
    let fg = other.unwrap_or_else(|| {
        tint_ink(
            lighting,
            colors,
            (base(dominant), brightness[dominant]),
            65,
            bg,
        )
    });
    paint(char::from(ramp[index]), fg, bg);
    true
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

/// Mix a terminal colour toward white by `percent`, in its own colour depth.
fn lighten(color: Color, percent: u16) -> Color {
    let Some(rgb) = terminal_rgb(color) else {
        return color;
    };
    let percent = u32::from(percent.min(100));
    let rgb = rgb.map(|c| (u32::from(c) + (255 - u32::from(c)) * percent / 100) as u8);
    match color {
        Color::Rgb(..) => Color::Rgb(rgb[0], rgb[1], rgb[2]),
        Color::Indexed(_) => quantize(rgb, Depth::Indexed),
        _ => quantize(rgb, Depth::Ansi),
    }
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
    // Facial flat ink keeps its own packing; every other identity-bearing
    // Mon cell takes the full-voxel ASCII overlay.
    if !flat_ink.iter().any(|ink| *ink)
        && paint_overlay_cell((renderer, colors, lighting), pixels, surface, scene, cell)
    {
        return;
    }
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
        Renderer::HalfBlock => match (shaded[0], shaded[1]) {
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
        },
        Renderer::Quadrant | Renderer::Braille => {
            cell.set_fg(shaded[first].unwrap());
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
    let face = face_clearance(sprite, renderer, projection, area);
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
                    surface[i] = projected_surface_light(sprite, projection, (px, py), face);
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

/// The head under a face tier's strokes as the viewer sees it, held in fixed
/// histograms so a frame allocates nothing: the luma of each texel's rendered
/// color (its material lit by the body's own model when lighting is enabled,
/// flat palette material when it is not) and its palette material.
struct HeadHistogram {
    luma: [u32; 256],
    material: [u32; 256],
    total: u32,
}

impl HeadHistogram {
    /// A region the sprite does not map falls back to the whole mapped head,
    /// then to a legacy face's anchor.
    fn of(
        sprite: &Sprite,
        colors: &Colors,
        lighting: &crate::lighting::VoxelLighting,
        region: InkRegion,
    ) -> Self {
        let text_box = FaceTexture::new(sprite).map(|texture| texture.text_fraction());
        let in_region = |x, y| match region {
            InkRegion::NativeBand => valid_head_uv(sprite, x, y)
                .is_some_and(|uv| (uv[1] - NATIVE_EYE_LINE).abs() <= NATIVE_BAND_REACH),
            InkRegion::TextBox => text_box.is_some_and(|fraction| {
                valid_face_uv(sprite, x, y).is_some_and(|uv| {
                    (uv[0] - 0.5).abs() <= fraction[0] / 2.0
                        && (uv[1] - 0.5).abs() <= fraction[1] / 2.0
                })
            }),
        };
        let mut head = Self::scan(sprite, colors, lighting, in_region);
        if head.total == 0 {
            head = Self::scan(sprite, colors, lighting, |x, y| {
                valid_head_uv(sprite, x, y).is_some() || valid_face_uv(sprite, x, y).is_some()
            });
        }
        if head.total == 0 {
            let anchor = sprite
                .face_overlay()
                .and_then(|face| sprite.pixel(u32::from(face.center_x), u32::from(face.y), 1));
            if let Some(index) = anchor {
                head.add(colors.ink(usize::from(index)), index);
            }
        }
        head
    }

    fn scan(
        sprite: &Sprite,
        colors: &Colors,
        lighting: &crate::lighting::VoxelLighting,
        include: impl Fn(u32, u32) -> bool,
    ) -> Self {
        let mut head = Self {
            luma: [0; 256],
            material: [0; 256],
            total: 0,
        };
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
                head.add(seen, index);
            }
        }
        head
    }

    fn add(&mut self, seen: Color, index: u8) {
        // Monochrome has no luma: it never counts toward legibility.
        if let Some(luma) = luminance(seen) {
            self.luma[luma.min(255) as usize] += 1;
        }
        self.material[usize::from(index)] += 1;
        self.total += 1;
    }

    /// Texels on which `candidate` is at least `minimum` luma (and never less
    /// than one step) away from the head under it.
    fn legible_on(&self, candidate: Color, minimum: u32) -> u32 {
        let Some(luma) = luminance(candidate) else {
            return 0;
        };
        let minimum = minimum.max(1);
        (0..256u32)
            .filter(|head| head.abs_diff(luma) >= minimum)
            .map(|head| self.luma[head as usize])
            .sum()
    }

    /// The most common palette material, the earlier index on a tie.
    fn dominant_material(&self) -> Option<u8> {
        (0..=255u8)
            .filter(|index| self.material[usize::from(*index)] > 0)
            .fold(None, |best: Option<u8>, index| match best {
                Some(most)
                    if self.material[usize::from(most)] >= self.material[usize::from(index)] =>
                {
                    best
                }
                _ => Some(index),
            })
    }
}

/// Face strokes use the dark face ink while it reads on at least half of the
/// head under them as rendered (lit, or flat when lighting is disabled),
/// measured by the configured outline separation. On a head too dark for it --
/// a black-bodied species, or shading that darkens a mid-tone body toward the
/// ink -- the candidate legible on the most texels wins, the earlier on a tie:
/// the ink, the dominant head material's own brightest/darkest lit rims
/// (lighting enabled only), then the palette's materials. Monochrome has no
/// luma and keeps the ink; only test-built Colors without lighting parameters
/// keep it unconditionally. Bounded: one sprite scan into fixed histograms.
fn face_ink(sprite: &Sprite, colors: &Colors, region: InkRegion) -> Color {
    let ink = colors.ink(0);
    let Some(lighting) = colors.lighting.as_ref() else {
        return ink;
    };
    let head = HeadHistogram::of(sprite, colors, lighting, region);
    let minimum = outline_separation(lighting);
    if head.legible_on(ink, minimum) * 2 >= head.total {
        return ink;
    }
    let rims = head
        .dominant_material()
        .filter(|_| lighting.enabled)
        .map(|index| {
            let base = colors.ink(usize::from(index));
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
            let count = head.legible_on(candidate, minimum);
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

/// Kaomoji eyes flank the mouth: text indices `len/2 - 1` and `len/2 + 1`
/// (the mouth is `len/2`, the parentheses are the ends).
fn eye_slot(text: &str, index: usize) -> bool {
    let middle = text.len() / 2;
    text.len() >= 3 && (index + 1 == middle || index == middle + 1)
}

/// Open eyes read as eyes on a light sclera plate. Closed, squinting, crying
/// and tick eyes (`- = ^ u ~ > < T ; x`, and the ticks) never take one, so a
/// blink or a wink stays readable.
fn open_eye(glyph: u8) -> bool {
    matches!(glyph, b'o' | b'O' | b'@' | b'Q' | b'*' | b'+' | b'.')
}

/// Whether kaomoji glyph `index` is an open eye, and so carries a plate.
fn plated_eye(text: &str, index: usize) -> bool {
    eye_slot(text, index) && text.as_bytes().get(index).copied().is_some_and(open_eye)
}

/// Grid rows -1..=9 of a sclera plate, one bit per column -1..=5 (bit c + 1).
type ScleraRows = [u8; 11];

/// An eye glyph's sclera plate, derived from its own bitmap rather than drawn:
/// the 5x9 glyph with its enclosed counters filled, dilated by one grid unit
/// (3x3), so `o` takes a round plate, `+` a plus-shaped one and `.` a 3x3
/// dot. It reaches one unit into each side bearing (columns -1..=5 of the
/// eight-column glyph box), never into the mouth or a parenthesis.
fn sclera_rows(glyph: u8) -> Option<ScleraRows> {
    let rows = face_glyph(glyph)?;
    let ink = |x: i32, y: i32| {
        (0..5).contains(&x) && (0..9).contains(&y) && rows[y as usize] & (1 << (4 - x)) != 0
    };
    let inside = |x: i32, y: i32| (-1..=5).contains(&x) && (-1..=9).contains(&y);
    // Flood the blank background in from the padded border; the blanks it
    // cannot reach are the glyph's counters.
    let mut outside = [[false; 7]; 11];
    let mut pending = Vec::new();
    for y in -1..=9 {
        for x in -1..=5 {
            if x == -1 || x == 5 || y == -1 || y == 9 {
                pending.push((x, y));
            }
        }
    }
    while let Some((x, y)) = pending.pop() {
        if !inside(x, y) || ink(x, y) || outside[(y + 1) as usize][(x + 1) as usize] {
            continue;
        }
        outside[(y + 1) as usize][(x + 1) as usize] = true;
        pending.extend([(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)]);
    }
    let filled = |x: i32, y: i32| inside(x, y) && !outside[(y + 1) as usize][(x + 1) as usize];
    let mut plate = [0u8; 11];
    for y in -1..=9 {
        for x in -1..=5 {
            if (-1..=1).any(|oy| (-1..=1).any(|ox| filled(x + ox, y + oy))) {
                plate[(y + 1) as usize] |= 1 << (x + 1);
            }
        }
    }
    Some(plate)
}

/// `sclera_rows` for every ASCII glyph, computed once.
fn sclera_plate(glyph: u8) -> ScleraRows {
    static PLATES: std::sync::OnceLock<[ScleraRows; 128]> = std::sync::OnceLock::new();
    PLATES.get_or_init(|| std::array::from_fn(|g| sclera_rows(g as u8).unwrap_or([0; 11])))
        [usize::from(glyph & 127)]
}

/// Eye-white colours. The plate is the SSOT palette's "eyes" entry (index 2)
/// while it separates from the outline ink by the configured outline
/// separation, else the palette's brightest entry; the pupil ink is the
/// outline ink while it separates from the plate by that much, else the
/// first palette entry that does. Never the head-based substitute ink (a
/// light rim on a dark head would vanish on white). Monochrome has no
/// colours and reverses video instead.
#[derive(Clone, Copy, Debug, PartialEq)]
struct Sclera {
    /// Palette indices of the plate and of the pupil ink (atlas pixels).
    index: u8,
    ink_index: u8,
    white: Color,
    ink: Color,
    mono: bool,
}

impl Sclera {
    fn of(colors: &Colors) -> Self {
        let mono = colors.palette.iter().all(|c| *c == Color::Reset);
        let minimum = colors
            .lighting
            .as_ref()
            .map_or(1, outline_separation)
            .max(1);
        let outline = colors.ink(0);
        let index = if mono
            || (colors.palette.len() > 2 && separation(colors.ink(2), outline) >= minimum)
        {
            2
        } else {
            (0..colors.palette.len())
                .max_by_key(|&i| {
                    (
                        luminance(colors.palette[i]).unwrap_or(0),
                        std::cmp::Reverse(i),
                    )
                })
                .unwrap_or(2)
        };
        let white = colors.ink(index);
        let ink_index = if mono || separation(outline, white) >= minimum {
            0
        } else {
            (0..colors.palette.len())
                .find(|&i| {
                    colors.palette[i] != white && separation(colors.palette[i], white) >= minimum
                })
                .or_else(|| {
                    (0..colors.palette.len()).max_by_key(|&i| {
                        (separation(colors.palette[i], white), std::cmp::Reverse(i))
                    })
                })
                .unwrap_or(0)
        };
        Self {
            index: index as u8,
            ink_index: ink_index as u8,
            white,
            ink: colors.ink(ink_index),
            mono,
        }
    }
}

/// An open-eye cell on its sclera plate: the stroke in pupil ink on the eye
/// white (a plate cell without a stroke draws `' '`). Monochrome cells are
/// reverse video, so the stroke reads dark on a light block.
fn paint_plate_cell(cell: &mut ratatui::buffer::Cell, sclera: Sclera, glyph: char) {
    cell.set_char(glyph);
    if sclera.mono {
        cell.set_style(
            ratatui::style::Style::default()
                .add_modifier(ratatui::style::Modifier::BOLD | ratatui::style::Modifier::REVERSED),
        );
    } else {
        cell.set_fg(sclera.ink).set_bg(sclera.white);
    }
}

/// A face character replacing a full quadrant/braille pattern keeps the body
/// colour that pattern showed as its background, so the room never shows
/// through the face behind the character.
fn back_with_body(renderer: Renderer, cell: &mut ratatui::buffer::Cell) {
    if matches!(renderer, Renderer::Quadrant | Renderer::Braille) && !cell.symbol().is_ascii() {
        let body = cell.fg;
        cell.set_bg(body);
    }
}

/// The face pass's background contract for one cell: unchanged, except an
/// open-eye plate (eye white with pupil ink or head colour, in either
/// polarity) and a character or stroke over a quadrant/braille pattern, which
/// keeps the head colour the pattern showed (braille may swap the planes so
/// the majority colour is the background). Anything else would be a painted
/// face rectangle.
#[cfg(test)]
fn face_background_kept(
    renderer: Renderer,
    colors: &Colors,
    before: &ratatui::buffer::Cell,
    after: &ratatui::buffer::Cell,
) -> bool {
    let sclera = Sclera::of(colors);
    let body = before.fg;
    let plate = !sclera.mono
        && [
            (sclera.ink, sclera.white),
            (sclera.white, sclera.ink),
            (sclera.white, body),
            (body, sclera.white),
        ]
        .contains(&(after.fg, after.bg));
    let packed =
        matches!(renderer, Renderer::Quadrant | Renderer::Braille) && !before.symbol().is_ascii();
    after.bg == before.bg
        || plate
        || (packed && (after.bg == body || (renderer == Renderer::Braille && after.fg == body)))
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

    /// Grid units (including the one-unit margin) to texture UV.
    fn grid_uv(self, grid: [f64; 2]) -> [f64; 2] {
        let size = self.grid_box();
        let scale = self.grid_per_uv();
        std::array::from_fn(|i| (grid[i] + 1.0 - size[i] / 2.0) / scale[i] + 0.5)
    }

    /// The glyph whose cell (five ink columns and their side bearing) holds
    /// a texture UV.
    fn glyph_at(self, uv: [f64; 2]) -> usize {
        let size = self.grid_box();
        let scale = self.grid_per_uv();
        let grid = scale[0] * (uv[0] - 0.5) + size[0] / 2.0 - 1.0;
        ((grid - 2.5) / 8.0)
            .round()
            .clamp(0.0, (self.text.len().max(1) - 1) as f64) as usize
    }

    /// The centre of a glyph's ink in texture UV; None for an inkless glyph.
    fn glyph_centroid(self, index: usize) -> Option<[f64; 2]> {
        let mut sum = [0.0; 2];
        let mut bits = 0.0;
        for y in 0..9 {
            for column in 0..5 {
                let x = index as i32 * 8 + column;
                if self.bit(x, y) {
                    sum[0] += f64::from(x) + 0.5;
                    sum[1] += f64::from(y) + 0.5;
                    bits += 1.0;
                }
            }
        }
        (bits > 0.0).then(|| self.grid_uv(sum.map(|v| v / bits)))
    }

    /// One glyph box (eight columns by the eleven-row grid) in texture UV.
    fn glyph_reach(self) -> [f64; 2] {
        let scale = self.grid_per_uv();
        [8.0 / scale[0], 11.0 / scale[1]]
    }

    /// Whether grid unit (x, y) lies on an open eye's sclera plate. Plates
    /// exist only for the eye slots and only for open eyes.
    fn sclera_bit(self, x: i32, y: i32) -> bool {
        if !(-1..=9).contains(&y) || self.text.len() < 3 {
            return false;
        }
        let middle = self.text.len() / 2;
        [middle - 1, middle + 1].into_iter().any(|index| {
            let column = x - 8 * index as i32;
            (-1..=5).contains(&column)
                && plated_eye(self.text, index)
                && sclera_plate(self.text.as_bytes()[index])[(y + 1) as usize] & (1 << (column + 1))
                    != 0
        })
    }

    /// Box-filtered share of glyph ink under a footprint, 0..=1. The atlas caps
    /// its filter at 0.75 grid units; the shaded tier filters a whole cell.
    fn coverage(self, uv: [f64; 2], footprint: [f64; 2], max_radius: f64) -> f64 {
        self.filtered(uv, footprint, max_radius, |x, y| self.bit(x, y))
    }

    /// The same box filter over the open eyes' sclera plates, through the
    /// same UV mapping, so a plate tracks, foreshortens and is occluded with
    /// the glyph it backs.
    fn sclera_coverage(self, uv: [f64; 2], footprint: [f64; 2], max_radius: f64) -> f64 {
        self.filtered(uv, footprint, max_radius, |x, y| self.sclera_bit(x, y))
    }

    fn filtered(
        self,
        uv: [f64; 2],
        footprint: [f64; 2],
        max_radius: f64,
        bit: impl Fn(i32, i32) -> bool,
    ) -> f64 {
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
                if bit(x, y) {
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

fn projected_face_frame(
    sprite: &Sprite,
    projection: Projection,
    x: u32,
    y: u32,
) -> Option<([f64; 2], [f64; 2], [f64; 2])> {
    projected_surface_frame(
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
    let (uv, per_x, per_y) =
        projected_surface_frame(sprite, projection, x, y, lookup, max_step, planar)?;
    let footprint = std::array::from_fn(|i| per_x[i].abs() + per_y[i].abs());
    Some((uv, footprint))
}

/// The UV at a packed pixel's centre and its change per packed screen pixel
/// along x and along y: the plane's local frame, so a cell can be sampled
/// below its packed resolution without leaving the pixel's authority.
fn projected_surface_frame(
    sprite: &Sprite,
    projection: Projection,
    x: u32,
    y: u32,
    lookup: fn(&Sprite, u32, u32) -> Option<[f64; 2]>,
    max_step: f64,
    planar: bool,
) -> Option<([f64; 2], [f64; 2], [f64; 2])> {
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
    Some((
        refined,
        horizontal.map(|d| d / projection.scale),
        vertical.map(|d| d / projection.scale_y),
    ))
}

type NativeHeadCell = ((u16, u16), [f64; 2], f64);

/// Native characters read as one word: overlay shading glyphs on the complete
/// head cells beside and between them, on their rows, are cleared to the
/// voxel's own lit colour, so no ramp glyph reads as an extra eye or mouth.
/// Only overlay cells (lit, identity-bearing voxels) are cleared; legacy
/// packing and monochrome keep their glyphs (a blank would erase the surface).
fn quiet_native_run(
    (buf, sprite, colors): (&mut Buffer, &Sprite, &Colors),
    painted: &[(u16, u16)],
    visible: &[NativeHeadCell],
) {
    let overlay = colors.lighting.as_ref().is_some_and(|light| light.enabled)
        && sprite.voxel_extent_pixels().is_some();
    if !overlay || colors.palette.iter().all(|c| *c == Color::Reset) {
        return;
    }
    for &(pos, _, _) in visible {
        let row: Vec<u16> = painted
            .iter()
            .filter(|p| p.1 == pos.1)
            .map(|p| p.0)
            .collect();
        let (Some(&low), Some(&high)) = (row.iter().min(), row.iter().max()) else {
            continue;
        };
        if painted.contains(&pos) || pos.0 + 1 < low || pos.0 > high + 1 {
            continue;
        }
        if let Some(cell) = buf.cell_mut(pos) {
            let overlay_glyph = cell
                .symbol()
                .chars()
                .next()
                .is_some_and(|c| c.is_ascii_graphic());
            if overlay_glyph && cell.bg != Color::Reset {
                cell.set_char(' ');
            }
        }
    }
}
type NativeGlyphCell = Option<((u16, u16), u8)>;

/// Every cell a native layout places a glyph on, whichever glyphs a blend
/// paints: the shading is cleared around the whole expression either way, so
/// a glyph another tier draws never loses its cell to the clearing.
fn placed_cells(candidates: &[NativeGlyphCell]) -> Vec<(u16, u16)> {
    candidates.iter().flatten().map(|(pos, _)| *pos).collect()
}

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
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
enum FaceLod {
    /// Terminal characters: one cell per glyph.
    Native,
    /// The glyph bitmap as multi-cell ASCII shading.
    Shaded,
    /// The glyph bitmap in packed pixels.
    Atlas,
}

/// Cells per glyph and rows the letterboxed text box needs for each tier.
/// Below the shaded minimum a glyph cannot be drawn across several cells, so
/// native characters stay. From three cells per glyph and three rows every
/// glyph has a stroke cell per segment (a paren's top, side and bottom, an
/// eye beside a mouth on the row below), so the kaomoji is drawn as
/// structural ASCII strokes on the face plane; packed-pixel renderers switch
/// to the pixel atlas only for large close-ups, at eight cells per glyph and
/// eight rows. One-pixel-per-cell ASCII would only stamp binary marks there
/// and keeps the strokes at every larger size.
const SHADED_MIN_CELLS_PER_GLYPH: f64 = 3.0;
const SHADED_MIN_ROWS: f64 = 3.0;
const ATLAS_MIN_CELLS_PER_GLYPH: f64 = 8.0;
const ATLAS_MIN_ROWS: f64 = 8.0;
/// A cell shows face ink only when its glyph coverage reaches this share.
const SHADED_MIN_INK: f64 = 0.15;

/// Where the letterboxed kaomoji sits on the face plane. A close-up that
/// shows only part of the plane re-letterboxes the text box (same aspect,
/// uniform shrink, centre held as near UV 0.5 as the visible part allows)
/// into what the viewport shows, so no glyph is cut by the panel edge. The
/// expression stays a texture on the head: only its placement on the plane
/// changes, and an unclipped face is exactly `WHOLE`.
#[derive(Clone, Copy, Debug, PartialEq)]
struct FaceFit {
    center: [f64; 2],
    shrink: f64,
}

impl FaceFit {
    const WHOLE: Self = Self {
        center: [0.5, 0.5],
        shrink: 1.0,
    };

    /// Face-plane UV to the UV of the standard letterboxed texture.
    fn map(self, uv: [f64; 2]) -> [f64; 2] {
        std::array::from_fn(|i| (uv[i] - self.center[i]) / self.shrink + 0.5)
    }

    fn footprint(self, footprint: [f64; 2]) -> [f64; 2] {
        footprint.map(|f| f / self.shrink)
    }

    /// The fit for the face plane as projected into `area`: the visible UV
    /// range is the plane's own range on every side the viewport does not
    /// cut, and the range of wholly visible face cells on a side it does.
    fn of(
        sprite: &Sprite,
        renderer: Renderer,
        projection: Projection,
        area: Rect,
        texture: FaceTexture,
        plane: [f64; 4],
    ) -> Self {
        let [left, top, right, bottom] = plane;
        let cut = [
            left < f64::from(area.x),
            top < f64::from(area.y),
            right > f64::from(area.right()),
            bottom > f64::from(area.bottom()),
        ];
        if !cut.iter().any(|c| *c) {
            return Self::WHOLE;
        }
        let (dx, dy) = renderer.packing();
        let mut low = [f64::INFINITY; 2];
        let mut high = [f64::NEG_INFINITY; 2];
        for row in (top.floor() as i32).max(i32::from(area.y))
            ..(bottom.ceil() as i32).min(i32::from(area.bottom()))
        {
            for col in (left.floor() as i32).max(i32::from(area.x))
                ..(right.ceil() as i32).min(i32::from(area.right()))
            {
                for y in 0..dy {
                    for x in 0..dx {
                        let px = col as u32 * u32::from(dx) + u32::from(x);
                        let py = row as u32 * u32::from(dy) + u32::from(y);
                        if let Some((uv, footprint)) = projected_face_uv(sprite, projection, px, py)
                        {
                            for (i, (low, high)) in low.iter_mut().zip(&mut high).enumerate() {
                                *low = low.min(uv[i] - footprint[i] / 2.0);
                                *high = high.max(uv[i] + footprint[i] / 2.0);
                            }
                        }
                    }
                }
            }
        }
        if !(low[0] < high[0] && low[1] < high[1]) {
            return Self::WHOLE;
        }
        // Screen x follows u and screen y follows v on a facing plane.
        let low = [
            if cut[0] { low[0].max(0.0) } else { 0.0 },
            if cut[1] { low[1].max(0.0) } else { 0.0 },
        ];
        let high = [
            if cut[2] { high[0].min(1.0) } else { 1.0 },
            if cut[3] { high[1].min(1.0) } else { 1.0 },
        ];
        let half = texture.text_fraction().map(|f| f / 2.0);
        if (0..2).all(|i| 0.5 - half[i] >= low[i] - 1e-9 && 0.5 + half[i] <= high[i] + 1e-9) {
            return Self::WHOLE;
        }
        let shrink = (0..2)
            .map(|i| (high[i] - low[i]) / (2.0 * half[i]))
            .fold(1.0f64, f64::min)
            .max(0.05);
        let center = std::array::from_fn(|i| {
            let reach = half[i] * shrink;
            0.5f64.clamp(low[i] + reach, (high[i] - reach).max(low[i] + reach))
        });
        Self { center, shrink }
    }
}

/// A 3x3 stroke mask, row-major from the top-left; each row's bits are
/// left = 4, middle = 2, right = 1.
const fn stroke_mask(rows: [u8; 3]) -> u16 {
    let mut mask = 0;
    let mut row = 0;
    while row < 3 {
        let mut column = 0;
        while column < 3 {
            if rows[row] & (4 >> column) != 0 {
                mask |= 1 << (row * 3 + column);
            }
            column += 1;
        }
        row += 1;
    }
    mask
}

/// The ASCII stroke alphabet of the shaded face, simplest strokes first: a
/// cell draws the character whose shape is nearest its sampled stroke mask,
/// so a paren reads as `/ ( \`, an eye as a ring and a mouth as `_`, rather
/// than as shading densities.
const STROKE_GLYPHS: [(char, u16); 32] = [
    ('|', stroke_mask([2, 2, 2])),
    ('-', stroke_mask([0, 7, 0])),
    ('_', stroke_mask([0, 0, 7])),
    ('/', stroke_mask([1, 2, 4])),
    ('\\', stroke_mask([4, 2, 1])),
    ('(', stroke_mask([2, 4, 2])),
    (')', stroke_mask([2, 1, 2])),
    ('(', stroke_mask([1, 4, 1])),
    (')', stroke_mask([4, 1, 4])),
    ('|', stroke_mask([4, 4, 4])),
    ('|', stroke_mask([1, 1, 1])),
    ('-', stroke_mask([7, 0, 0])),
    ('/', stroke_mask([3, 4, 4])),
    ('/', stroke_mask([3, 4, 0])),
    ('\\', stroke_mask([6, 1, 1])),
    ('\\', stroke_mask([6, 1, 0])),
    ('\\', stroke_mask([4, 4, 3])),
    ('\\', stroke_mask([0, 4, 3])),
    ('/', stroke_mask([1, 1, 6])),
    ('/', stroke_mask([0, 1, 6])),
    ('/', stroke_mask([0, 1, 4])),
    ('\\', stroke_mask([0, 4, 1])),
    ('.', stroke_mask([0, 0, 2])),
    ('\'', stroke_mask([2, 0, 0])),
    ('o', stroke_mask([0, 2, 0])),
    ('^', stroke_mask([2, 5, 0])),
    ('v', stroke_mask([0, 5, 2])),
    ('=', stroke_mask([7, 0, 7])),
    ('+', stroke_mask([2, 7, 2])),
    ('x', stroke_mask([5, 2, 5])),
    ('O', stroke_mask([7, 5, 7])),
    ('#', stroke_mask([7, 7, 7])),
];

fn stroke_glyph(mask: u16, source: u8) -> char {
    STROKE_GLYPHS
        .iter()
        .filter(|(glyph, _)| !matches!(source, b'(' | b')') || "/\\\\|()'.-_".contains(*glyph))
        .min_by_key(|(_, shape)| (shape ^ mask).count_ones())
        .map_or('#', |(glyph, _)| *glyph)
}

/// The tier a face takes with no remembered tier (the exact thresholds).
#[cfg(test)]
fn face_lod(texture: FaceTexture, columns: f64, rows: f64, pixel_atlas: bool) -> FaceLod {
    face_tier(face_metric(texture, columns, rows), pixel_atlas, None)
}

/// Cells per glyph and rows of the letterboxed text box.
fn face_metric(texture: FaceTexture, columns: f64, rows: f64) -> [f64; 2] {
    let fraction = texture.text_fraction();
    [
        columns * fraction[0] / texture.text.len() as f64,
        rows * fraction[1],
    ]
}

/// Schmitt widths of the face tiers, used once a live camera remembers the
/// tier it drew. A native face enters the strokes only from 3.5 cells per
/// glyph and 3.5 rows, and leaves them only below the 3.0 minimum the strokes
/// need; an atlas face is entered at 8 and left only below 7. A metric that
/// settles or jitters on a threshold (zoom easing, a turntable, a head bob)
/// therefore never flips the tier. Without a memory the exact minimums apply.
const SHADED_ENTRY_CELLS_PER_GLYPH: f64 = 3.5;
const SHADED_ENTRY_ROWS: f64 = 3.5;
const ATLAS_EXIT_CELLS_PER_GLYPH: f64 = 7.0;
const ATLAS_EXIT_ROWS: f64 = 7.0;

/// The tier a face of `[cells per glyph, rows]` takes after `previous`.
fn face_tier([per_glyph, rows]: [f64; 2], pixel_atlas: bool, previous: Option<FaceLod>) -> FaceLod {
    let shaded = if previous == Some(FaceLod::Native) {
        [SHADED_ENTRY_CELLS_PER_GLYPH, SHADED_ENTRY_ROWS]
    } else {
        [SHADED_MIN_CELLS_PER_GLYPH, SHADED_MIN_ROWS]
    };
    let atlas = if previous == Some(FaceLod::Atlas) {
        [ATLAS_EXIT_CELLS_PER_GLYPH, ATLAS_EXIT_ROWS]
    } else {
        [ATLAS_MIN_CELLS_PER_GLYPH, ATLAS_MIN_ROWS]
    };
    if per_glyph < shaded[0] || rows < shaded[1] {
        FaceLod::Native
    } else if pixel_atlas && per_glyph >= atlas[0] && rows >= atlas[1] {
        FaceLod::Atlas
    } else {
        FaceLod::Shaded
    }
}

impl FaceLod {
    /// Position on the detail scale a blend moves along.
    fn level(self) -> f32 {
        match self {
            Self::Native => 0.0,
            Self::Shaded => 1.0,
            Self::Atlas => 2.0,
        }
    }

    fn at(level: usize) -> Self {
        match level {
            0 => Self::Native,
            1 => Self::Shaded,
            _ => Self::Atlas,
        }
    }
}

/// The face detail one camera subject drew last, kept by the UI between
/// frames (presentation only, never saved; a new pet or preview starts from
/// `Default`). It holds the Schmitt tier the face is heading for and the
/// drawn level its blend started from. The level eases to that tier with the
/// camera's smoothstep, one tier per configured zoom transition, glyph by
/// glyph, so no zoom, orbit, resize or view change swaps a whole face between
/// native characters, structural strokes and the atlas in one frame, and a
/// blend interrupted by another change continues from what was drawn.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct DetailMemo {
    tier: Option<FaceLod>,
    from: f32,
    started: usize,
    /// The last measured `[cells per glyph, rows]` (diagnostics and tests).
    metric: [f32; 2],
}

/// The UI clock a face blend runs on: the animation frame and the configured
/// `zoom_transition_frames`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct DetailClock {
    pub frame: usize,
    pub frames: usize,
}

/// A live camera's remembered face detail. None (snapshots, one-off renders)
/// takes the exact tier thresholds with no blend.
pub type FaceDetail<'a> = Option<(&'a std::cell::Cell<DetailMemo>, DetailClock)>;

impl DetailMemo {
    /// The level drawn at `clock` (0 native, 1 strokes, 2 atlas). A blend
    /// takes `frames` per tier it crosses, so the level never moves more than
    /// the smoothstep's peak of 1.5 / `frames` tiers in one frame.
    fn level(self, clock: DetailClock) -> Option<f32> {
        let to = self.tier?.level();
        let length = clock.frames.max(1) as f32 * (to - self.from).abs();
        let elapsed = clock.frame.saturating_sub(self.started) as f32;
        if elapsed >= length {
            return Some(to);
        }
        let t = elapsed / length;
        Some(self.from + (to - self.from) * t * t * (3.0 - 2.0 * t))
    }

    /// Head for the tier `target` picks from the remembered one. A new tier
    /// starts its blend from the level drawn now; the first frame of a
    /// subject takes its tier at once.
    fn step(
        self,
        clock: DetailClock,
        metric: [f64; 2],
        target: impl Fn(Option<FaceLod>) -> FaceLod,
    ) -> (Self, f32) {
        let tier = target(self.tier);
        let mut next = match self.level(clock) {
            Some(_) if self.tier == Some(tier) => self,
            Some(level) => Self {
                tier: Some(tier),
                from: level,
                started: clock.frame,
                ..self
            },
            None => Self {
                tier: Some(tier),
                from: tier.level(),
                started: clock.frame,
                ..self
            },
        };
        next.metric = metric.map(|m| m as f32);
        (next, next.level(clock).unwrap_or_else(|| tier.level()))
    }
}

/// Kaomoji glyphs one face tier draws: bit `i` draws glyph `i`.
type GlyphSet = u64;
const ALL_GLYPHS: GlyphSet = GlyphSet::MAX;

fn draws_glyph(glyphs: GlyphSet, index: usize) -> bool {
    index >= 64 || glyphs & (1 << index) != 0
}

/// The share of a tier step past which glyph `index` of `len` has switched:
/// the middle glyph first, then each symmetric pair outwards (the eyes, then
/// the parentheses or cheeks), so a blend is a mix of whole glyphs that keeps
/// the expression symmetric and readable on every frame.
fn glyph_switch(index: usize, len: usize) -> f32 {
    let rings = len.div_ceil(2).max(1);
    let ring = (2 * index).abs_diff(len.saturating_sub(1)) / 2;
    (ring as f32 + 0.5) / rings as f32
}

/// The two tiers a drawn level lies between, and the glyphs already on the
/// upper one.
fn face_blend(level: f32, len: usize) -> (FaceLod, FaceLod, GlyphSet) {
    let base = level.floor().clamp(0.0, 1.0);
    let share = level - base;
    let upper = (0..len.min(64))
        .filter(|&i| share > glyph_switch(i, len))
        .fold(0, |set, i| set | (1 << i));
    (
        FaceLod::at(base as usize),
        FaceLod::at(base as usize + 1),
        upper,
    )
}

/// Intermediate and large shaded faces: the kaomoji drawn as structural ASCII
/// strokes on the face plane. Each cell wholly on the plane samples the glyph
/// bitmap at a 3x3 sub-cell grid through the plane's local UV frame and draws
/// the stroke character nearest that shape (`STROKE_GLYPHS`), so a paren is a
/// curve of `/ ( \`, an eye a ring and a mouth a `_` run instead of a cloud of
/// shading densities. A glyph too small for strokes (one or two rows, at most
/// three columns) is its own native character at its ink centroid; a glyph
/// whose ink lands only on cells it may not paint (a mouth under a muzzle) is
/// placed as its native character on the nearest wholly face-plane cell within
/// one glyph box. Only cells wholly on the mapped face plane take part, so
/// noses, occluders and rear views keep their surface, and a cell whose
/// coverage is below `SHADED_MIN_INK` stays body. One-plane ASCII restores the
/// lit body exactly first, so stale marks never survive in stroke gaps;
/// packed cells keep their pixels as the background behind the stroke and
/// face ink. Returns whether any stroke was laid.
fn draw_shaded_face(
    sprite: &Sprite,
    renderer: Renderer,
    colors: &Colors,
    projection: Projection,
    area: Rect,
    buf: &mut Buffer,
    face: (FaceTexture, [f64; 4], Color, FaceFit, GlyphSet, GlyphSet),
) -> bool {
    struct ShadedCell {
        pos: (u16, u16),
        centre: [f64; 2],
        mask: u16,
        glyph: Option<char>,
        /// The kaomoji glyph the drawn character belongs to.
        source: Option<usize>,
        /// Mean sclera-plate coverage of the cell's packed pixels.
        sclera: f64,
        original: [Option<u8>; 8],
        surface: [Option<SurfaceLight>; 8],
    }
    // `glyphs` are drawn; `collapsed` ones as their own single character.
    let (texture, [left, top, right, bottom], stroke_color, fit, glyphs, collapsed) = face;
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
            let mut sclera = 0.0;
            let mut centre = [0.0; 2];
            let mut covered = 0;
            for y in 0..dy {
                for x in 0..dx {
                    let i = usize::from(y * dx + x);
                    let px = col as u32 * u32::from(dx) + u32::from(x);
                    let py = row as u32 * u32::from(dy) + u32::from(y);
                    original[i] = projection.sample(sprite, px, py);
                    surface[i] =
                        projected_surface_light(sprite, projection, (px, py), Some((texture, fit)));
                    // Each packed pixel filters its own footprint; the cell is their mean.
                    if let Some((uv, footprint)) = projected_face_uv(sprite, projection, px, py) {
                        covered += 1;
                        let uv = fit.map(uv);
                        let footprint = fit.footprint(footprint);
                        ink += texture.coverage(uv, footprint, f64::INFINITY) / count;
                        sclera += texture.sclera_coverage(uv, footprint, f64::INFINITY) / count;
                        for (dest, value) in centre.iter_mut().zip(uv) {
                            *dest += value / count;
                        }
                    }
                }
            }
            if covered == dx * dy {
                let mask = if ink >= SHADED_MIN_INK {
                    face_stroke_mask(
                        sprite,
                        projection,
                        (texture, fit),
                        (col as u32, row as u32),
                        (dx, dy),
                    )
                } else {
                    0
                };
                cells.push(ShadedCell {
                    pos: (col as u16, row as u16),
                    centre,
                    mask,
                    glyph: None,
                    source: None,
                    sclera,
                    original,
                    surface,
                });
            }
        }
    }
    let reach = texture.glyph_reach();
    let distance = |a: [f64; 2], b: [f64; 2]| {
        (((a[0] - b[0]) / reach[0]).powi(2) + ((a[1] - b[1]) / reach[1]).powi(2)).sqrt()
    };
    for (index, glyph) in texture.text.bytes().enumerate() {
        let Some(target) = texture.glyph_centroid(index) else {
            continue;
        };
        let members: Vec<usize> = (0..cells.len())
            .filter(|&c| cells[c].mask != 0 && texture.glyph_at(cells[c].centre) == index)
            .collect();
        if members.is_empty() {
            // The glyph's ink fell only on cells it may not paint: keep the
            // feature, as its own character on the nearest face-plane cell.
            let nearest = (0..cells.len())
                .filter(|&c| cells[c].mask == 0 && cells[c].glyph.is_none())
                .map(|c| (c, distance(cells[c].centre, target)))
                .filter(|(_, d)| *d <= 1.0)
                .min_by(|a, b| a.1.total_cmp(&b.1));
            if let Some((c, _)) = nearest {
                cells[c].glyph = Some(char::from(glyph));
                cells[c].source = Some(index);
            }
            continue;
        }
        let span = |axis: fn((u16, u16)) -> u16| {
            let values = members.iter().map(|&c| axis(cells[c].pos));
            let low = values.clone().min().unwrap_or(0);
            values.max().unwrap_or(0) - low + 1
        };
        if draws_glyph(collapsed, index) || (span(|p| p.1) <= 2 && span(|p| p.0) <= 3) {
            // A horizontal mark keeps a run of three or more on the row it
            // covers most; anything shorter is one character.
            let row = members
                .iter()
                .map(|&c| cells[c].pos.1)
                .max_by_key(|row| {
                    let n = members.iter().filter(|&&c| cells[c].pos.1 == *row).count();
                    (n, *row)
                })
                .unwrap_or(0);
            let run: Vec<usize> = members
                .iter()
                .copied()
                .filter(|&c| cells[c].pos.1 == row)
                .collect();
            let keep: Vec<usize> = if matches!(glyph, b'_' | b'-' | b'~' | b'=') && run.len() >= 3 {
                run
            } else {
                members
                    .iter()
                    .copied()
                    .min_by(|&a, &b| {
                        distance(cells[a].centre, target)
                            .total_cmp(&distance(cells[b].centre, target))
                    })
                    .into_iter()
                    .collect()
            };
            for c in members {
                cells[c].mask = 0;
            }
            for c in keep {
                cells[c].glyph = Some(char::from(glyph));
                cells[c].source = Some(index);
            }
        } else {
            for c in members {
                cells[c].glyph = Some(stroke_glyph(cells[c].mask, glyph));
                cells[c].source = Some(index);
            }
        }
    }
    // Fewer than three marks are no face: leave the head to the native
    // glyphs or the caption rather than claim one.
    if cells.iter().filter(|cell| cell.glyph.is_some()).count() < 3 {
        return false;
    }
    // A packed cell's background already holds body pixels, so relighting it
    // would not reproduce the body; ASCII's background is the room's.
    let restore = (dx, dy) == (1, 1);
    let mono = colors.palette.iter().all(|c| *c == Color::Reset);
    let sclera = Sclera::of(colors);
    let mut stroked = false;
    for cell in cells {
        let Some(target) = buf.cell_mut(cell.pos) else {
            continue;
        };
        if restore {
            let background = target.bg;
            paint_lit_pixels(
                renderer,
                colors,
                cell.original,
                cell.surface,
                [false; 8],
                target,
            );
            target.set_bg(background);
        }
        // A blend draws each glyph from one tier: another tier owns this one.
        let owner = cell.source.unwrap_or_else(|| texture.glyph_at(cell.centre));
        if !draws_glyph(glyphs, owner) {
            continue;
        }
        // Eye white: a cell wholly on the face plane that is mostly on an open
        // eye's plate, or that draws an open eye's own stroke (its ink lies
        // inside its plate, so a dot eye collapsed to one character keeps
        // one). A mouth or parenthesis stroke is never whitened.
        let plate = match cell.source {
            Some(index) => plated_eye(texture.text, index),
            None => cell.sclera >= 0.5,
        };
        if plate {
            paint_plate_cell(target, sclera, cell.glyph.unwrap_or(' '));
            stroked |= cell.glyph.is_some();
            continue;
        }
        let Some(glyph) = cell.glyph else {
            continue;
        };
        back_with_body(renderer, target);
        // Monochrome has no ink colour: a stroke never repeats the body glyph.
        let body = target.symbol().chars().next();
        let glyph = if mono && body == Some(glyph) {
            ['#', '@', 'O']
                .into_iter()
                .find(|g| Some(*g) != body)
                .unwrap_or('#')
        } else {
            glyph
        };
        paint_face_character(target, colors, stroke_color, glyph);
        stroked = true;
    }
    stroked
}

/// The 3x3 sub-cell stroke mask of one face cell, sampled through the
/// plane's local UV frame at its first packed pixel. A cell whose coverage
/// passed the ink threshold but whose sub-samples all miss a thin stroke keeps
/// its strongest sub-sample, so it never draws an empty shape.
fn face_stroke_mask(
    sprite: &Sprite,
    projection: Projection,
    (texture, fit): (FaceTexture, FaceFit),
    (col, row): (u32, u32),
    (dx, dy): (u16, u16),
) -> u16 {
    let Some((uv, per_x, per_y)) =
        projected_face_frame(sprite, projection, col * u32::from(dx), row * u32::from(dy))
    else {
        return 1 << 4;
    };
    let (w, h) = (f64::from(dx), f64::from(dy));
    // `spread` is the sample pitch in cells: a third samples the cell itself;
    // a wider pitch looks past a stroke that fills the cell to its direction.
    let sample = |spread: f64| {
        let footprint = std::array::from_fn(|i| (per_x[i].abs() * w + per_y[i].abs() * h) / 3.0);
        let mut mask = 0u16;
        let mut best = (0.0, 4);
        for j in 0..3 {
            for i in 0..3 {
                let ox = (0.5 + (f64::from(i) - 1.0) * spread) * w - 0.5;
                let oy = (0.5 + (f64::from(j) - 1.0) * spread) * h - 0.5;
                let at = std::array::from_fn(|k| uv[k] + per_x[k] * ox + per_y[k] * oy);
                let coverage = texture.coverage(fit.map(at), fit.footprint(footprint), 0.75);
                let bit = (j * 3 + i) as u16;
                if coverage >= 0.3 {
                    mask |= 1 << bit;
                }
                if coverage > best.0 {
                    best = (coverage, bit);
                }
            }
        }
        if mask == 0 {
            mask = 1 << best.1;
        }
        mask
    };
    let mask = sample(1.0 / 3.0);
    if mask.count_ones() >= 6 {
        let context = sample(0.75);
        if context.count_ones() < 7 {
            return context;
        }
    }
    mask
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
    draw_surface_glyphs_of(sprite, renderer, colors, projection, area, buf, ALL_GLYPHS)
}

/// `draw_surface_glyphs` for the glyphs of `glyphs` only. Placement still
/// lays out the whole expression, so a blended glyph stays exactly where its
/// native character goes.
fn draw_surface_glyphs_of(
    sprite: &Sprite,
    renderer: Renderer,
    colors: &Colors,
    projection: Projection,
    area: Rect,
    buf: &mut Buffer,
    glyphs: GlyphSet,
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
    let style = (renderer, colors, texture.text, ink, glyphs);
    if area.height <= 3 {
        let candidates = native_micro_candidates(texture, &visible);
        let placed = placed_cells(&candidates);
        let painted = paint_native_glyphs(buf, style, candidates);
        quiet_native_run((buf, sprite, colors), &placed, &visible);
        return !painted.is_empty();
    }
    if let Some(run) = native_run_candidates(texture, &visible) {
        let placed = placed_cells(&run);
        let painted = paint_native_glyphs(buf, style, run);
        quiet_native_run((buf, sprite, colors), &placed, &visible);
        return !painted.is_empty();
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
        let placed = placed_cells(&candidates);
        let painted = paint_native_glyphs(buf, style, candidates);
        quiet_native_run((buf, sprite, colors), &placed, &visible);
        return !painted.is_empty();
    }
    if let Some(candidates) = compact {
        let placed = placed_cells(&candidates);
        paint_native_glyphs(buf, style, candidates);
        quiet_native_run((buf, sprite, colors), &placed, &visible);
        return true;
    }
    false
}

/// Native characters on their complete head cells, indexed by kaomoji glyph:
/// an open eye is its own one-cell sclera plate (so tiny Room, phone and
/// watch faces keep noticeable eyes); every other character is face ink over
/// the body. Returns the painted cells.
fn paint_native_glyphs(
    buf: &mut Buffer,
    (renderer, colors, text, ink, glyphs): (Renderer, &Colors, &str, Color, GlyphSet),
    candidates: Vec<NativeGlyphCell>,
) -> Vec<(u16, u16)> {
    let sclera = Sclera::of(colors);
    let mut painted = Vec::new();
    for (index, candidate) in candidates.into_iter().enumerate() {
        let Some((pos, glyph)) = candidate.filter(|_| draws_glyph(glyphs, index)) else {
            continue;
        };
        if let Some(cell) = buf.cell_mut(pos) {
            if plated_eye(text, index) {
                paint_plate_cell(cell, sclera, char::from(glyph));
            } else {
                // Preserve the room plane, including terminal tint/opacity.
                back_with_body(renderer, cell);
                paint_face_character(cell, colors, ink, char::from(glyph));
            }
            painted.push(pos);
        }
    }
    painted
}

/// Where the kaomoji lies on the face plane in a viewport, and its size.
#[derive(Clone, Copy, Debug)]
struct FaceLayout {
    /// The face plane's projected cell rectangle (left, top, right, bottom).
    rect: [f64; 4],
    fit: FaceFit,
    /// Cells per glyph and rows of the fitted text box.
    metric: [f64; 2],
    stroke_color: Color,
    /// The renderer has a packed-pixel atlas tier for this face.
    pixel_atlas: bool,
}

impl FaceLayout {
    fn of(
        sprite: &Sprite,
        renderer: Renderer,
        colors: &Colors,
        projection: Projection,
        area: Rect,
        texture: FaceTexture,
    ) -> Option<Self> {
        let bounds = FaceTexture::bounds(sprite)?;
        // Low detail uses independent UV anchors; fine detail samples the
        // atlas. A severely minified/foreshortened surface keeps the separate
        // HUD caption rather than forcing a five-character billboard into an
        // unresolvable head.
        let (left, top) = projection.cell(bounds.left, bounds.top, renderer);
        let (right, bottom) = projection.cell(bounds.right, bounds.bottom, renderer);
        // A close-up that shows only part of the face plane fits the kaomoji
        // to that part, so tiers follow the expression's actual drawn size.
        let rect = [left, top, right, bottom];
        let fit = FaceFit::of(sprite, renderer, projection, area, texture, rect);
        let columns = (right - left) * fit.shrink;
        let rows = (bottom - top) * fit.shrink;
        // Native cells use full visible head coverage, including the
        // forehead. Resolution comes from the letterboxed text box on the
        // narrow face surface: native characters, then structural ASCII
        // strokes, then the atlas. One pixel per cell has no sub-cell atlas to
        // switch to, and half-block atlas ink is a flat palette pixel: a head
        // that needs a substitute ink (see face_ink) keeps the shaded
        // strokes, whose glyphs carry it.
        let stroke_color = face_ink(sprite, colors, InkRegion::TextBox);
        let pixel_atlas = renderer.packing() != (1, 1)
            && !(renderer == Renderer::HalfBlock && stroke_color != colors.ink(0));
        Some(Self {
            rect,
            fit,
            metric: face_metric(texture, columns, rows),
            stroke_color,
            pixel_atlas,
        })
    }
}

/// Large expressions are textures on the actual head surface, transformed and
/// occluded by the sprite's depth-tested UV map, with glyph LOD on small heads.
/// One stateless frame: the exact tier thresholds, no blend.
#[cfg(test)]
fn draw_camera_face(
    sprite: &Sprite,
    renderer: Renderer,
    colors: &Colors,
    projection: Projection,
    area: Rect,
    buf: &mut Buffer,
) -> bool {
    draw_camera_face_with(sprite, renderer, colors, projection, area, buf, None)
}

/// `draw_camera_face` for a live camera that remembers its face detail: the
/// tier follows the Schmitt thresholds and blends glyph by glyph over the
/// transition frames (see `DetailMemo`). Every glyph of a blended frame is
/// drawn whole by one tier, so the expression stays readable throughout.
fn draw_camera_face_with(
    sprite: &Sprite,
    renderer: Renderer,
    colors: &Colors,
    projection: Projection,
    area: Rect,
    buf: &mut Buffer,
    detail: FaceDetail<'_>,
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
    let Some(layout) = FaceLayout::of(sprite, renderer, colors, projection, area, texture) else {
        return native_fits && draw_surface_glyphs(sprite, renderer, colors, projection, area, buf);
    };
    let FaceLayout {
        rect,
        fit,
        metric,
        stroke_color,
        pixel_atlas,
    } = layout;
    let level = match detail {
        None => face_tier(metric, pixel_atlas, None).level(),
        Some((memo, clock)) => {
            let (next, level) = memo.get().step(clock, metric, |previous| {
                face_tier(metric, pixel_atlas, previous)
            });
            memo.set(next);
            level
        }
    };
    // A renderer without an atlas tier blends back from one at most.
    let level = if pixel_atlas { level } else { level.min(1.0) };
    let len = texture.text.len();
    let (low, high, upper) = face_blend(level, len);
    let every = if len >= 64 {
        ALL_GLYPHS
    } else {
        (1 << len) - 1
    };
    let face = (texture, rect, stroke_color, fit);
    let tier = |tier: FaceLod, glyphs: GlyphSet, buf: &mut Buffer| {
        let native = |buf: &mut Buffer| {
            native_fits
                && draw_surface_glyphs_of(sprite, renderer, colors, projection, area, buf, glyphs)
        };
        match tier {
            FaceLod::Native => native(buf),
            // A face plane turned nearly edge-on leaves no cell wholly on it,
            // so the overlay lays no stroke: show native glyphs on the visible
            // head instead, or report no face so the caption carries it.
            FaceLod::Shaded => {
                draw_shaded_face(
                    sprite,
                    renderer,
                    colors,
                    projection,
                    area,
                    buf,
                    (face.0, face.1, face.2, face.3, glyphs, 0),
                ) || native(buf)
            }
            FaceLod::Atlas => draw_atlas_face(
                sprite,
                renderer,
                colors,
                projection,
                area,
                buf,
                (face, glyphs),
            ),
        }
    };
    if upper & every == 0 {
        return tier(low, ALL_GLYPHS, buf);
    }
    if upper & every == every {
        return tier(high, ALL_GLYPHS, buf);
    }
    if low == FaceLod::Native {
        // Native characters and strokes share one layout: the glyphs not yet
        // on strokes collapse to their own character inside their glyph box
        // on the face plane (as glyphs too small for strokes do), so the
        // expression grows stroke by stroke in place. A separate native run
        // would sit on the eye line apart from the strokes.
        return draw_shaded_face(
            sprite,
            renderer,
            colors,
            projection,
            area,
            buf,
            (texture, rect, stroke_color, fit, ALL_GLYPHS, !upper),
        ) || tier(low, ALL_GLYPHS, buf);
    }
    // Strokes and atlas: each tier draws its own glyphs on its own copy; a cell takes
    // the upper tier's change unless the lower tier drew there too, where the
    // tier holding most glyphs wins.
    let base = buf.clone();
    let mut above = buf.clone();
    let lower = tier(low, !upper, buf);
    let higher = tier(high, upper, &mut above);
    let upper_wins = 2 * (upper & every).count_ones() as usize > len;
    for row in area.y..area.bottom() {
        for col in area.x..area.right() {
            let (was, new) = (&base[(col, row)], &above[(col, row)]);
            if new == was {
                continue;
            }
            let cell = &mut buf[(col, row)];
            if *cell == *was || upper_wins {
                *cell = new.clone();
            }
        }
    }
    lower || higher
}

/// The atlas tier: the glyph bitmap (and the open eyes' plates) in packed
/// pixels on the face plane, for the glyphs of `glyphs`.
fn draw_atlas_face(
    sprite: &Sprite,
    renderer: Renderer,
    colors: &Colors,
    projection: Projection,
    area: Rect,
    buf: &mut Buffer,
    face: ((FaceTexture, [f64; 4], Color, FaceFit), GlyphSet),
) -> bool {
    let ((texture, [left, top, right, bottom], stroke_color, fit), glyphs) = face;
    let (dx, dy) = renderer.packing();
    let mono = colors.palette.iter().all(|c| *c == Color::Reset);
    let sclera = Sclera::of(colors);
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
            // On an open eye's sclera plate (its ink included): the same
            // depth-tested face UV authorizes the plate and the stroke.
            let mut plate = [false; 8];
            let mut covered = [false; 8];
            for y in 0..dy {
                for x in 0..dx {
                    let i = usize::from(y * dx + x);
                    let px = col as u32 * u32::from(dx) + u32::from(x);
                    let py = row as u32 * u32::from(dy) + u32::from(y);
                    pixels[i] = projection.sample(sprite, px, py);
                    original[i] = pixels[i];
                    surface[i] =
                        projected_surface_light(sprite, projection, (px, py), Some((texture, fit)));
                    if let Some((uv, footprint)) = projected_face_uv(sprite, projection, px, py) {
                        covered[i] = true;
                        let (uv, footprint) = (fit.map(uv), fit.footprint(footprint));
                        let own = draws_glyph(glyphs, texture.glyph_at(uv));
                        ink[i] = own && texture.sample(uv, footprint);
                        plate[i] = own && texture.sclera_coverage(uv, footprint, 0.75) >= 0.5;
                        pixels[i] = match (ink[i], plate[i]) {
                            (true, true) => Some(sclera.ink_index),
                            (true, false) => Some(0),
                            (false, true) => Some(sclera.index),
                            (false, false) => pixels[i],
                        };
                    }
                }
            }
            if !covered.iter().any(|v| *v) {
                continue;
            }
            let count = usize::from(dx * dy);
            let full_cell = covered[..count].iter().all(|v| *v);
            let white = (0..count).filter(|&i| plate[i] && !ink[i]).count();
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
                    if plate[0] {
                        paint_plate_cell(cell, sclera, if ink[0] { '#' } else { ' ' });
                    } else if ink[0] {
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
                    if plate[0] || plate[1] {
                        let glyph = if ink[0] || ink[1] { '@' } else { ' ' };
                        paint_plate_cell(cell, sclera, glyph);
                    } else if ink[0] || ink[1] {
                        cell.set_char(if cell.symbol() == "@" { '#' } else { '@' });
                    }
                }
                Renderer::HalfBlock => {
                    // Plate and pupil pixels are flat palette pixels, like ink.
                    let flat = std::array::from_fn(|i| ink[i] || plate[i]);
                    paint_lit_pixels(renderer, colors, pixels, surface, flat, cell)
                }
                Renderer::Quadrant | Renderer::Braille => {
                    // Mixed cells keep the real nose/ear/occluder untouched.
                    if !full_cell {
                        continue;
                    }
                    let background = cell.bg;
                    paint_lit_pixels(renderer, colors, original, surface, [false; 8], cell);
                    cell.set_bg(background);
                    let any_ink = ink.iter().any(|on| *on);
                    // An inkless, plateless cell is the body itself (overlay
                    // included); drawing no stroke would only flatten it.
                    if !any_ink && white == 0 {
                        continue;
                    }
                    let pattern = |mask: [bool; 8]| {
                        if renderer == Renderer::Quadrant {
                            quadrant(std::array::from_fn(|i| mask[i]))
                        } else {
                            braille(mask)
                        }
                    };
                    let backing = cell.fg;
                    let on_plate = plate.iter().any(|on| *on);
                    // Two colours per cell. Braille dots are small, so the
                    // majority colour takes the background there and a
                    // plate's interior reads white, a thick ring dark.
                    let two = |cell: &mut ratatui::buffer::Cell, on: [bool; 8], fg, bg| {
                        let n = (0..count).filter(|&i| on[i]).count();
                        if renderer == Renderer::Braille && n * 2 > count {
                            cell.set_char(pattern(on.map(|v| !v))).set_fg(bg).set_bg(fg);
                        } else {
                            cell.set_char(pattern(on)).set_fg(fg).set_bg(bg);
                        }
                    };
                    if mono {
                        if on_plate && (any_ink || white * 2 >= count) {
                            // Reverse video: ink dark on a light plate.
                            paint_plate_cell(cell, sclera, pattern(ink));
                        } else if any_ink {
                            // No ink colour: carve the strokes from the body.
                            cell.set_char(pattern(ink.map(|v| !v))).set_fg(backing);
                        }
                    } else if any_ink && on_plate {
                        // The pupil in its ink on the eye white.
                        two(cell, ink, sclera.ink, sclera.white);
                    } else if any_ink {
                        // Strokes in face ink over the head's own lit colour,
                        // never holes through to the room behind the Mon.
                        two(cell, ink, stroke_color, backing);
                    } else {
                        // The plate's own edge, pixel-exact against the head.
                        two(cell, plate, sclera.white, backing);
                    }
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
    /// The live camera's remembered face detail; None renders one stateless
    /// frame with the exact tier thresholds.
    pub face_detail: FaceDetail<'a>,
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
    detail: FaceDetail<'_>,
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
    let painted =
        draw_camera_face_with(sprite, renderer, colors, projection, viewport, buf, detail);
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
                pan_permille: self.camera.pan_permille,
                elevation_radians: self.camera.elevation_radians,
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
        let face = face_clearance(self.sprite, self.renderer, projection, viewport);
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
                                overlay: None,
                            });
                        }
                        if let Some((sx, sy)) = projection.source_pixel(self.sprite, px, py) {
                            if let Some(index) = self.sprite.pixel(sx, sy, 1) {
                                if world
                                    .is_none_or(|w| f64::from(w.depth) <= depth_at(sx, sy) + 0.0001)
                                {
                                    pet_wins = true;
                                    pixels[i] = Some(index);
                                    surface[i] = projected_surface_light(
                                        self.sprite,
                                        projection,
                                        (px, py),
                                        face,
                                    );
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
            self.face_detail,
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
        let face_painted = draw_camera_face_with(
            self.sprite,
            self.renderer,
            self.colors,
            projection,
            viewport,
            buf,
            self.face_detail,
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
            overlay: None,
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
                            // (@_@): open eyes sit on their plates in pupil
                            // ink; every other mark is the dark face ink.
                            let sclera = Sclera::of(&colors);
                            let plate = !sclera.mono && after[(x, y)].bg == sclera.white;
                            assert_eq!(
                                after[(x, y)].fg,
                                if plate {
                                    sclera.ink
                                } else {
                                    face_ink(&sprite, &colors, InkRegion::NativeBand)
                                }
                            );
                            assert!(face_background_kept(
                                renderer,
                                &colors,
                                &before[(x, y)],
                                &after[(x, y)]
                            ));
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
            face_detail: None,
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
                face_detail: None,
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
                        face_detail: None,
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
                face_detail: None,
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
                    face_detail: None,
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
                        face_detail: None,
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
                        face_detail: None,
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
                face_detail: None,
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
                            // A cleared overlay glyph beside native text is no glyph.
                            if after[(col, row)].symbol() != " " {
                                glyphs.push(after[(col, row)].symbol().to_owned());
                            }
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
                    // A cleared overlay glyph beside native text is no mark.
                    .filter(|(_, (a, b))| a != b && b.symbol() != " ")
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
                            ..Camera::default()
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
                            assert!(face_background_kept(
                                renderer,
                                &colors,
                                &before[(x, y)],
                                &after[(x, y)]
                            ));
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
            let indexed = Colors::from_config(&Config::sample().unwrap(), Depth::Indexed).unwrap();
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
                            assert!(face_background_kept(
                                renderer,
                                &indexed,
                                &before[(x, y)],
                                &after[(x, y)]
                            ));
                            // A cleared overlay glyph beside native text is no mark.
                            if before[(x, y)] != after[(x, y)] && after[(x, y)].symbol() != " " {
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
                                    ..Camera::default()
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
                                    assert!(face_background_kept(
                                        renderer,
                                        &colors,
                                        &before[(x, y)],
                                        &after[(x, y)]
                                    ));
                                    // A cleared overlay glyph beside native text is no mark.
                                    if before[(x, y)] != after[(x, y)]
                                        && after[(x, y)].symbol() != " "
                                    {
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
        let indexed = Colors::from_config(&Config::sample().unwrap(), Depth::Indexed).unwrap();
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
                        assert!(
                            face_background_kept(renderer, &indexed, before, after),
                            "{renderer:?}: Pet {columns}"
                        );
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
                            face_detail: None,
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
                let fit = FaceFit::of(
                    &sprite,
                    renderer,
                    projection,
                    area,
                    texture,
                    [left, top, right, bottom],
                );
                let shaded = face_lod(
                    texture,
                    (right - left) * fit.shrink,
                    (bottom - top) * fit.shrink,
                    (dx, dy) != (1, 1),
                ) == FaceLod::Shaded;
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
                                    shaded || !texture.sample(fit.map(uv), fit.footprint(footprint))
                                });
                                if let Some((uv, footprint)) = sample {
                                    ink += texture.coverage(
                                        fit.map(uv),
                                        fit.footprint(footprint),
                                        f64::INFINITY,
                                    ) / f64::from(dx * dy);
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
                            // Quadrant/braille strokes keep the head's own lit
                            // colour (the source pattern's) behind them.
                            let mut body = before[(col, row)].clone();
                            body.set_fg(source[(col, row)].fg)
                                .set_symbol(source[(col, row)].symbol());
                            assert!(
                                face_background_kept(renderer, &colors, &body, &buf[(col, row)]),
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
                        face_detail: None,
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
                face_detail: None,
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
                    &mut all,
                    None
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
                        None,
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
        overlay: Option<OverlaySample>,
    ) -> Option<SurfaceLight> {
        Some(SurfaceLight {
            normal,
            occlusion,
            edge: screen_edge,
            screen_edge,
            overlay,
        })
    }

    /// An identity-bearing interior sample of material 1 on voxel [3, 4, 5].
    fn voxel(normal: [f32; 3], span: f32) -> OverlaySample {
        let facet = normal.map(|v| (v.clamp(-1.0, 1.0) * 127.0).round() as i8);
        OverlaySample {
            voxel: [3, 4, 5],
            facet,
            material: 1,
            depth: 0.0,
            voxel_span: [span; 2],
            outside: 0,
            contour: 0,
            deep_contour: 0,
            crease: 0,
            seam: 0,
            neighbours: [(facet, 1); 4],
            face_plane: false,
        }
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
    fn identity_bearing_voxels_carry_ascii_shading_and_identityless_samples_stay_legacy() {
        // Replaces the old sparse-texture contract (only magnified, uniform,
        // dark, non-head cells were glyphed): every full identity-bearing
        // voxel cell now carries the filtered ramp over its lit colour.
        let light = light();
        let colors = Colors {
            palette: vec![Color::Rgb(12, 16, 24), Color::Rgb(120, 80, 40)],
            background: Color::Rgb(1, 2, 3),
            lighting: Some(light.clone()),
        };
        let (ramp, len) = shading_ramp(&light);
        let ramp = &ramp[..len];
        assert!(!ramp.contains(&b'-'), "the line alphabet never shades");
        let shaded = |symbol: &str| symbol.bytes().next().is_some_and(|c| ramp.contains(&c));
        let shadow = [0.0, -1.0, 0.0];
        let lit = [-0.45, 0.65, 0.45];
        for renderer in [
            Renderer::Ascii,
            Renderer::HalfBlock,
            Renderer::Quadrant,
            Renderer::Braille,
        ] {
            let (dx, dy) = renderer.packing();
            let count = usize::from(dx * dy);
            let full: [Option<u8>; 8] = std::array::from_fn(|i| (i < count).then_some(1));
            let cell_of = |normal: [f32; 3], occlusion: u8, overlay: Option<OverlaySample>| {
                let samples: [Option<SurfaceLight>; 8] = std::array::from_fn(|i| {
                    if i < count {
                        sample(normal, occlusion, false, overlay)
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
            for (normal, occlusion) in [(shadow, 80), (lit, 0)] {
                let cell = paint(
                    full,
                    cell_of(normal, occlusion, Some(voxel(normal, 1.0))),
                    [false; 8],
                );
                assert!(
                    shaded(cell.symbol()),
                    "{renderer:?} {normal:?}: full voxel {:?}",
                    cell.symbol()
                );
                let body = shade_terminal_color(
                    &light,
                    colors.ink(1),
                    light.brightness(normal, occlusion, false),
                );
                assert_eq!(
                    cell.bg, body,
                    "{renderer:?}: the voxel is the cell background"
                );
                assert!(
                    separation(cell.fg, cell.bg) >= hatch_separation(&light),
                    "{renderer:?}: the shading glyph is visible"
                );
            }
            // Negative controls: no identity, face ink and the quiet face
            // zone keep their own packing; a partial cell keeps the scene.
            let mut quiet = voxel(shadow, 1.0);
            quiet.face_plane = true;
            for (label, cell) in [
                (
                    "no identity",
                    paint(full, cell_of(shadow, 80, None), [false; 8]),
                ),
                (
                    "face ink",
                    paint(
                        full,
                        cell_of(shadow, 80, Some(voxel(shadow, 1.0))),
                        [true; 8],
                    ),
                ),
            ] {
                assert!(
                    renderer == Renderer::Ascii || !shaded(cell.symbol()),
                    "{renderer:?} {label}: {:?}",
                    cell.symbol()
                );
                // Legacy packing keeps the scene as the background (half-block
                // draws its two samples as the two colour planes instead).
                if renderer != Renderer::HalfBlock {
                    assert_ne!(
                        cell.bg,
                        shade_terminal_color(
                            &light,
                            colors.ink(1),
                            light.brightness(shadow, 80, false)
                        ),
                        "{renderer:?} {label}: legacy packing never fills the voxel background"
                    );
                }
            }
            let quiet_cell = paint(full, cell_of(shadow, 80, Some(quiet)), [false; 8]);
            assert!(
                !shaded(quiet_cell.symbol())
                    || (renderer == Renderer::Ascii && quiet_cell.symbol() == " "),
                "{renderer:?} quiet face zone: {:?}",
                quiet_cell.symbol()
            );
            if count > 1 {
                let mut partial = full;
                partial[count - 1] = None;
                let cell = paint(
                    partial,
                    cell_of(shadow, 80, Some(voxel(shadow, 1.0))),
                    [false; 8],
                );
                assert_eq!(
                    cell.bg, colors.background,
                    "{renderer:?}: partial keeps the scene"
                );
                assert!(
                    separation(cell.fg, colors.background) >= outline_separation(&light),
                    "{renderer:?}: partial outline ink separates from the scene"
                );
            }
        }
    }

    #[test]
    fn every_species_keeps_a_bounded_visible_silhouette_and_shading_never_reaches_faces() {
        // Rewritten for the full-voxel overlay: the mono silhouette is an
        // oriented outline glyph (no longer the densest ramp glyph), and the
        // old "texture only on uniform non-head interiors" rule becomes the
        // quiet zone: no shading glyph on the face plane or native eye band.
        let config = Config::sample().unwrap();
        let (ramp, len) = shading_ramp(&light());
        let ramp: Vec<char> = ramp[..len]
            .iter()
            .map(|b| char::from(*b))
            .filter(|c| *c != '.')
            .collect();
        let outline: Vec<char> = "-_|/\\^v<>.".chars().collect();
        let mut shaded_somewhere = false;
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
                            let face = face_clearance(&sprite, renderer, projection, area);
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
                            // Colour ASCII's quiet face zone is a blank glyph on
                            // the voxel's own lit background: still the Mon.
                            let occupied = |x: u16, y: u16| {
                                buf[(x, y)].symbol() != " " || buf[(x, y)].bg != Color::Reset
                            };
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
                                            (u32::from(x), u32::from(y)),
                                            face,
                                        )
                                        .is_some();
                                        if boundary && lit {
                                            let symbol = buf[(x, y)].symbol();
                                            assert!(
                                                symbol.chars().next().is_some_and(|c| outline.contains(&c)),
                                                "{label}: mono boundary at {x},{y} is {symbol:?}, not an outline glyph"
                                            );
                                        }
                                    }
                                    let symbol = buf[(x, y)].symbol();
                                    let shaded =
                                        symbol.chars().next().is_some_and(|c| ramp.contains(&c));
                                    if depth == Depth::Mono || !shaded {
                                        continue;
                                    }
                                    shaded_somewhere = true;
                                    for sy in 0..dy {
                                        for sx in 0..dx {
                                            let px = u32::from(x) * u32::from(dx) + u32::from(sx);
                                            let py = u32::from(y) * u32::from(dy) + u32::from(sy);
                                            let (ux, uy) = projection
                                                .source_pixel(&sprite, px, py)
                                                .unwrap_or_else(|| panic!("{label}: shading outside the Mon at {x},{y}"));
                                            assert!(
                                                sprite.pixel(ux, uy, 1).is_some(),
                                                "{label}: shading outside the Mon at {x},{y}"
                                            );
                                            let quiet = projected_surface_light(
                                                &sprite,
                                                projection,
                                                (px, py),
                                                face,
                                            )
                                            .and_then(|s| s.overlay)
                                            .is_some_and(|o| o.face_plane);
                                            assert!(
                                                !quiet,
                                                "{label}: shading glyph {symbol:?} in the quiet face zone at {x},{y}"
                                            );
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
        assert!(shaded_somewhere, "full voxels must carry ASCII shading");
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
                        ..Camera::default()
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
                                            ..Camera::default()
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
                                let head = HeadHistogram::of(&sprite, &colors, &lighting, region);
                                assert!(head.total > 0, "{stage:?} {style:?} shape {shape}");
                                let legible_on =
                                    |candidate: Color| head.legible_on(candidate, minimum);
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
                                    legible_on(ink) * 2 >= head.total || legible_on(ink) >= best,
                                    "{label}: {ink:?} reads on {}/{} texels, a palette ink on {best}",
                                    legible_on(ink),
                                    head.total
                                );
                                // The dark ink stays wherever it already reads.
                                if legible_on(colors.ink(0)) * 2 >= head.total {
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
                                            ..Camera::default()
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
    fn edge_on_faces_never_claim_a_face_they_did_not_draw() {
        // A steeply turned head (e.g. yaw 60 at high zoom) can leave no cell
        // wholly on the face plane, so the shaded overlay lays no stroke. The
        // face pass must then fall back to native glyphs or report nothing,
        // so the caption carries the expression -- never a silent blank head.
        // (Exact 90/270 profiles map no face at all: profile_symmetry_tests.)
        let config = Config::sample().unwrap();
        let colors = Colors::from_config(&config, Depth::Indexed).unwrap();
        let area = Rect::new(0, 0, 62, 30);
        let (mut shaded, mut native, mut captioned) = (0, 0, 0);
        for stage in [Stage::Baby, Stage::Adult] {
            for style in [SpriteStyle::EightBit, SpriteStyle::SixteenBit] {
                for shape in 0..16u8 {
                    for yaw in [0, 60, 75, 120, 240, 285, 300] {
                        let sprite = Sprite::voxel_with_appearance(
                            stage,
                            Expression::Happy,
                            style,
                            0,
                            yaw,
                            false,
                            Appearance {
                                shape,
                                body: shape,
                                accent: (shape + 5) % 16,
                            },
                        );
                        let (Some(texture), Some(bounds)) =
                            (FaceTexture::new(&sprite), FaceTexture::bounds(&sprite))
                        else {
                            continue;
                        };
                        let stroke = face_ink(&sprite, &colors, InkRegion::TextBox);
                        for renderer in [Renderer::Ascii, Renderer::HalfBlock] {
                            let pixel_atlas = renderer.packing() != (1, 1)
                                && !(renderer == Renderer::HalfBlock && stroke != colors.ink(0));
                            for view in [CameraView::Pet, CameraView::Face] {
                                for zoom_percent in (100..=400).step_by(50) {
                                    let projection = Projection::camera(
                                        &sprite,
                                        SolidBounds::of(&sprite).unwrap(),
                                        area,
                                        renderer,
                                        0,
                                        (500, 500),
                                        Camera {
                                            orbit_degrees: yaw,
                                            view,
                                            from_view: view,
                                            zoom_percent,
                                            transition_percent: 100,
                                            ..Camera::default()
                                        },
                                    );
                                    let (l, t) = projection.cell(bounds.left, bounds.top, renderer);
                                    let (r, b) =
                                        projection.cell(bounds.right, bounds.bottom, renderer);
                                    if face_lod(texture, r - l, b - t, pixel_atlas)
                                        != FaceLod::Shaded
                                    {
                                        continue;
                                    }
                                    shaded += 1;
                                    let mut body = Buffer::empty(area);
                                    draw_projection(
                                        &sprite, renderer, &colors, projection, area, &mut body,
                                    );
                                    let mut face = body.clone();
                                    let drew = draw_camera_face(
                                        &sprite, renderer, &colors, projection, area, &mut face,
                                    );
                                    let strokes = body
                                        .content
                                        .iter()
                                        .zip(&face.content)
                                        .filter(|(a, b)| a != b)
                                        .count();
                                    assert!(
                                        !drew || strokes >= 3,
                                        "{stage:?} {style:?} shape {shape} yaw {yaw} {renderer:?} {view:?} @{zoom_percent}: claimed a face, drew {strokes} cells"
                                    );
                                    // The overlay on its own: did it lay any stroke here?
                                    let mut overlay = body.clone();
                                    let stroked = draw_shaded_face(
                                        &sprite,
                                        renderer,
                                        &colors,
                                        projection,
                                        area,
                                        &mut overlay,
                                        (
                                            texture,
                                            [l, t, r, b],
                                            stroke,
                                            FaceFit::WHOLE,
                                            ALL_GLYPHS,
                                            0,
                                        ),
                                    );
                                    if !stroked {
                                        native += usize::from(drew);
                                        captioned += usize::from(!drew);
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        assert!(shaded > 0, "steep turns must exercise the shaded tier");
        // Structural strokes now cover the formerly native-fallback cases.
        // Refused overlays must still exercise the caption path. Native LOD's
        // connected eye/mouth contract is independently covered below.
        assert!(
            captioned > 0,
            "stroke-less overlays must retain caption fallback: native {native}, caption {captioned} of {shaded}"
        );
    }

    #[test]
    fn face_lod_tiers_are_ordered_and_take_the_atlas_only_for_large_faces() {
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
                            // Unfitted texture: the text box is the face box.
                            let per_glyph = columns / text.len() as f64;
                            let native = per_glyph < 3.0 || rows < 3.0;
                            let atlas = per_glyph >= 8.0 && rows >= 8.0;
                            let expected = if native {
                                FaceLod::Native
                            } else if atlas {
                                FaceLod::Atlas
                            } else {
                                FaceLod::Shaded
                            };
                            assert_eq!(lod, expected, "{text} {columns}x{rows}");
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
        // Strokes are the structural alphabet, the expression's own characters
        // (features too small for strokes) or the monochrome body-swap marks.
        let stroke_alphabet = |text: &str| -> Vec<char> {
            STROKE_GLYPHS
                .iter()
                .map(|(glyph, _)| *glyph)
                .chain(text.chars())
                .chain(['#', '@', 'O'])
                .collect()
        };
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
                                let fit = FaceFit::of(
                                    &front,
                                    renderer,
                                    projection,
                                    area,
                                    texture,
                                    [left, top, right, bottom],
                                );
                                if face_lod(
                                    texture,
                                    (right - left) * fit.shrink,
                                    (bottom - top) * fit.shrink,
                                    pixel_atlas,
                                ) != FaceLod::Shaded
                                {
                                    continue;
                                }
                                let alphabet = stroke_alphabet(texture.text);
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
                                                || !alphabet.contains(
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
                                            alphabet.contains(&glyph),
                                            "{label}: {glyph:?} at {x},{y} is not a stroke"
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

#[cfg(test)]
mod live_face_tests {
    use super::*;
    use crate::{
        game::Stage,
        sprite::{Appearance, Expression, SpriteStyle},
    };

    /// Root's live Face close-ups: the fox (seed 513: shape 1, body 8, accent
    /// 3) and the tanuki (seed 514) as Babies in detailed style, frontal, in
    /// the room panel of a 130x28 terminal (inner 77x9, viewport 75x7) and of a
    /// 120x40 terminal (inner 71x20). The 130x28 frames reproduce the live
    /// captures cell for cell.
    const LAYOUTS: [(&str, Rect, Rect); 2] = [
        ("130x28", Rect::new(0, 0, 130, 28), Rect::new(2, 5, 77, 9)),
        ("120x40", Rect::new(0, 0, 120, 40), Rect::new(2, 5, 71, 20)),
    ];
    const MONS: [Appearance; 2] = [
        Appearance {
            shape: 1,
            body: 8,
            accent: 3,
        },
        Appearance {
            shape: 2,
            body: 14,
            accent: 5,
        },
    ];

    fn viewport(area: Rect) -> Rect {
        Rect::new(area.x + 1, area.y + 1, area.width - 2, area.height - 2)
    }

    fn face_view(zoom_percent: u16) -> Camera {
        Camera {
            orbit_degrees: 45,
            view: CameraView::Face,
            from_view: CameraView::Face,
            zoom_percent,
            transition_percent: 100,
            ..Camera::default()
        }
    }

    /// The tier, the fit and the glyph each changed cell belongs to.
    struct Close {
        lod: FaceLod,
        texture: FaceTexture,
        cells: Vec<((u16, u16), char, usize)>,
    }

    fn close_up(
        sprite: &Sprite,
        renderer: Renderer,
        colors: &Colors,
        layout: (Rect, Rect),
        zoom_percent: u16,
    ) -> Close {
        let (whole, area) = layout;
        let view = viewport(area);
        let projection = Projection::camera(
            sprite,
            SolidBounds::of(sprite).unwrap(),
            view,
            renderer,
            0,
            (500, 700),
            face_view(zoom_percent),
        );
        let texture = FaceTexture::new(sprite).unwrap();
        let bounds = FaceTexture::bounds(sprite).unwrap();
        let (l, t) = projection.cell(bounds.left, bounds.top, renderer);
        let (r, b) = projection.cell(bounds.right, bounds.bottom, renderer);
        let fit = FaceFit::of(sprite, renderer, projection, view, texture, [l, t, r, b]);
        let stroke = face_ink(sprite, colors, InkRegion::TextBox);
        let pixel_atlas = renderer.packing() != (1, 1)
            && !(renderer == Renderer::HalfBlock && stroke != colors.ink(0));
        let lod = face_lod(
            texture,
            (r - l) * fit.shrink,
            (b - t) * fit.shrink,
            pixel_atlas,
        );
        let mut before = Buffer::empty(whole);
        draw_projection(sprite, renderer, colors, projection, view, &mut before);
        let mut after = before.clone();
        assert!(draw_camera_face(
            sprite, renderer, colors, projection, view, &mut after
        ));
        let (dx, dy) = renderer.packing();
        let mut cells = Vec::new();
        for row in whole.y..whole.bottom() {
            for col in whole.x..whole.right() {
                if before[(col, row)] == after[(col, row)] {
                    continue;
                }
                assert!(
                    view.contains((col, row).into()),
                    "{renderer:?} @{zoom_percent}: face paint at {col},{row} outside the viewport"
                );
                let mut centre = [0.0; 2];
                let mut covered = 0;
                for y in 0..dy {
                    for x in 0..dx {
                        let px = u32::from(col) * u32::from(dx) + u32::from(x);
                        let py = u32::from(row) * u32::from(dy) + u32::from(y);
                        if let Some((uv, _)) = projected_face_uv(sprite, projection, px, py) {
                            covered += 1;
                            for (dest, value) in centre.iter_mut().zip(fit.map(uv)) {
                                *dest += value / f64::from(dx * dy);
                            }
                        }
                    }
                }
                if lod == FaceLod::Native {
                    cells.push(((col, row), ' ', 0));
                    continue;
                }
                if lod == FaceLod::Shaded {
                    assert_eq!(
                        covered,
                        dx * dy,
                        "{renderer:?} @{zoom_percent}: stroke at {col},{row} leaves the face plane"
                    );
                }
                if covered == 0 {
                    continue;
                }
                let glyph = after[(col, row)].symbol().chars().next().unwrap_or(' ');
                cells.push(((col, row), glyph, texture.glyph_at(centre)));
            }
        }
        Close {
            lod,
            texture,
            cells,
        }
    }

    #[test]
    fn live_face_close_ups_draw_whole_structural_kaomoji() {
        let config = Config::sample().unwrap();
        let mut shaded = 0;
        for appearance in MONS {
            for frame in [0, 4, 6] {
                let sprite = Sprite::voxel_with_appearance(
                    Stage::Baby,
                    Expression::Idle,
                    SpriteStyle::SixteenBit,
                    frame,
                    0,
                    false,
                    appearance,
                );
                for (name, whole, area) in LAYOUTS {
                    for renderer in [
                        Renderer::HalfBlock,
                        Renderer::Ascii,
                        Renderer::Quadrant,
                        Renderer::Braille,
                    ] {
                        for depth in [Depth::Rgb, Depth::Mono] {
                            let colors = Colors::from_config(&config, depth).unwrap();
                            for zoom_percent in (200..=400).step_by(50) {
                                let label = format!(
                                    "{appearance:?} frame {frame} {name} {renderer:?} {depth:?} @{zoom_percent}"
                                );
                                let close = close_up(
                                    &sprite,
                                    renderer,
                                    &colors,
                                    (whole, area),
                                    zoom_percent,
                                );
                                let text = close.texture.text.as_bytes();
                                if name == "130x28" && zoom_percent == 200 {
                                    // Root's Face 200%: a 3.7-row text box is a
                                    // shaded face, not a one-row label.
                                    assert_eq!(close.lod, FaceLod::Shaded, "{label}");
                                }
                                if close.lod == FaceLod::Native {
                                    continue;
                                }
                                for (index, glyph) in text.iter().enumerate() {
                                    // The pixel atlas does not relocate a mouth
                                    // its muzzle covers (deferred); the strokes do.
                                    if close.lod == FaceLod::Atlas && index == text.len() / 2 {
                                        continue;
                                    }
                                    assert!(
                                        close.cells.iter().any(|c| c.2 == index),
                                        "{label}: glyph {} of {} has no stroke: {:?}",
                                        char::from(*glyph),
                                        close.texture.text,
                                        close.cells
                                    );
                                }
                                if close.lod != FaceLod::Shaded {
                                    continue;
                                }
                                shaded += 1;
                                let mono = depth == Depth::Mono;
                                for &(pos, glyph, index) in &close.cells {
                                    let swap = mono && matches!(glyph, '#' | '@' | 'O');
                                    assert!(
                                        swap || !matches!(glyph, ':' | '%' | '*' | '@'),
                                        "{label}: density glyph {glyph:?} at {pos:?}"
                                    );
                                    if matches!(text[index], b'(' | b')') {
                                        assert!(
                                            swap || "/\\|()'.-_".contains(glyph),
                                            "{label}: paren drawn with {glyph:?} at {pos:?}"
                                        );
                                    }
                                }
                                let columns = |index: usize| {
                                    close
                                        .cells
                                        .iter()
                                        .filter(move |c| c.2 == index)
                                        .map(|c| i32::from(c.0 .0))
                                };
                                let gap = columns(1)
                                    .flat_map(|a| columns(3).map(move |b| (b - a).abs()))
                                    .min()
                                    .unwrap();
                                assert!(gap >= 6, "{label}: eyes only {gap} columns apart");
                                for paren in [0, text.len() - 1] {
                                    let rows: Vec<u16> = close
                                        .cells
                                        .iter()
                                        .filter(|c| c.2 == paren)
                                        .map(|c| c.0 .1)
                                        .collect();
                                    let span =
                                        rows.iter().max().unwrap() - rows.iter().min().unwrap();
                                    assert!(
                                        span >= 2,
                                        "{label}: paren {paren} spans {} rows",
                                        span + 1
                                    );
                                }
                            }
                        }
                    }
                }
            }
        }
        assert!(shaded > 100, "only {shaded} shaded close-ups");
    }

    #[test]
    fn stroke_alphabet_matches_its_own_shapes_and_prefers_simple_strokes() {
        for (glyph, shape) in STROKE_GLYPHS {
            assert_eq!(stroke_glyph(shape, glyph as u8), glyph, "{shape:09b}");
        }
        assert_eq!(stroke_glyph(stroke_mask([2, 2, 0]), b'o'), '|');
        assert_eq!(stroke_glyph(stroke_mask([0, 3, 0]), b'o'), '-');
        assert_eq!(stroke_glyph(stroke_mask([0, 0, 3]), b'o'), '_');
        assert_eq!(stroke_glyph(stroke_mask([7, 7, 7]), b'o'), '#');
        // No stroke shape is a pure shading density.
        assert!(STROKE_GLYPHS
            .iter()
            .all(|(glyph, _)| !matches!(glyph, ':' | '%' | '*' | '@')));
    }

    #[test]
    fn faces_fit_the_visible_plane_and_stay_whole_when_unclipped() {
        let config = Config::sample().unwrap();
        let _colors = Colors::from_config(&config, Depth::Indexed).unwrap();
        let sprite = Sprite::voxel_with_appearance(
            Stage::Baby,
            Expression::Idle,
            SpriteStyle::SixteenBit,
            0,
            0,
            false,
            MONS[0],
        );
        let texture = FaceTexture::new(&sprite).unwrap();
        let bounds = FaceTexture::bounds(&sprite).unwrap();
        let (_, _, area) = LAYOUTS[1];
        let view = viewport(area);
        let mut fitted = 0;
        for renderer in [Renderer::Ascii, Renderer::HalfBlock] {
            for zoom_percent in [100, 400] {
                let projection = Projection::camera(
                    &sprite,
                    SolidBounds::of(&sprite).unwrap(),
                    view,
                    renderer,
                    0,
                    (500, 700),
                    face_view(zoom_percent),
                );
                let (l, t) = projection.cell(bounds.left, bounds.top, renderer);
                let (r, b) = projection.cell(bounds.right, bounds.bottom, renderer);
                let fit = FaceFit::of(&sprite, renderer, projection, view, texture, [l, t, r, b]);
                let inside = l >= f64::from(view.x)
                    && t >= f64::from(view.y)
                    && r <= f64::from(view.right())
                    && b <= f64::from(view.bottom());
                if inside {
                    assert_eq!(fit, FaceFit::WHOLE, "{renderer:?} @{zoom_percent}");
                    continue;
                }
                // The fitted text box lies inside the visible UV range.
                assert!(fit.shrink < 1.0, "{renderer:?} @{zoom_percent}: {fit:?}");
                fitted += 1;
                let half = texture.text_fraction().map(|f| f * fit.shrink / 2.0);
                let (cl, ct) = (
                    l + (r - l) * (fit.center[0] - half[0]),
                    t + (b - t) * (fit.center[1] - half[1]),
                );
                let (cr, cb) = (
                    l + (r - l) * (fit.center[0] + half[0]),
                    t + (b - t) * (fit.center[1] + half[1]),
                );
                assert!(
                    cl >= f64::from(view.x) - 1.0
                        && cr <= f64::from(view.right()) + 1.0
                        && ct >= f64::from(view.y) - 1.0
                        && cb <= f64::from(view.bottom()) + 1.0,
                    "{renderer:?} @{zoom_percent}: fitted box {cl:.1},{ct:.1}..{cr:.1},{cb:.1} vs {view:?}"
                );
            }
        }
        assert!(fitted > 0, "the 400% close-up must exercise the fit");
    }
}

#[cfg(test)]
mod voxel_overlay_tests {
    //! The full-voxel ASCII overlay against ground truth taken from the
    //! projection and the sprite's own geometry (occupancy, lattice voxels,
    //! normals, depth), never from the overlay's classifier.
    use super::*;
    use crate::{
        game::Stage,
        sprite::{Appearance, Expression, SpriteStyle},
        world::WorldRules,
    };

    const RENDERERS: [Renderer; 4] = [
        Renderer::Ascii,
        Renderer::HalfBlock,
        Renderer::Quadrant,
        Renderer::Braille,
    ];
    const OUTLINE: &str = "-_|/\\^v<>.";
    const LINES: &str = "-_|/\\^v<>";

    fn mon(stage: Stage, style: SpriteStyle, yaw: u16, shape: u8) -> Sprite {
        Sprite::voxel_with_appearance(
            stage,
            Expression::Idle,
            style,
            0,
            yaw,
            false,
            Appearance {
                shape,
                body: shape,
                accent: (shape + 5) % 16,
            },
        )
    }

    fn camera(view: CameraView, zoom_percent: u16) -> Camera {
        Camera {
            view,
            from_view: view,
            zoom_percent,
            transition_percent: 100,
            ..Camera::default()
        }
    }

    /// The bare Mon over a room-coloured scene, and its projection.
    fn project(
        sprite: &Sprite,
        renderer: Renderer,
        colors: &Colors,
        (view, zoom): (CameraView, u16),
        (width, height): (u16, u16),
    ) -> (Projection, Buffer, Color, FaceClearance) {
        let area = Rect::new(0, 0, width, height);
        let scene = colors.ink(10);
        let mut buf = Buffer::empty(area);
        for cell in &mut buf.content {
            cell.set_bg(scene);
        }
        let projection = Projection::camera(
            sprite,
            SolidBounds::of(sprite).unwrap(),
            area,
            renderer,
            0,
            (500, 700),
            camera(view, zoom),
        );
        draw_projection(sprite, renderer, colors, projection, area, &mut buf);
        let face = face_clearance(sprite, renderer, projection, area);
        (projection, buf, scene, face)
    }

    fn samples(renderer: Renderer, col: u16, row: u16) -> impl Iterator<Item = (usize, u32, u32)> {
        let (dx, dy) = renderer.packing();
        (0..dy).flat_map(move |y| {
            (0..dx).map(move |x| {
                (
                    usize::from(y * dx + x),
                    u32::from(col) * u32::from(dx) + u32::from(x),
                    u32::from(row) * u32::from(dy) + u32::from(y),
                )
            })
        })
    }

    fn identity(
        sprite: &Sprite,
        projection: Projection,
        px: u32,
        py: u32,
    ) -> Option<([i32; 3], [i8; 3])> {
        let (sx, sy) = projection.source_pixel(sprite, px, py)?;
        sprite.surface_facet(sx, sy)
    }

    /// The quiet face zone: the face plane within the fitted kaomoji's halo.
    fn quiet(
        sprite: &Sprite,
        projection: Projection,
        face: FaceClearance,
        px: u32,
        py: u32,
    ) -> bool {
        projection
            .source_pixel(sprite, px, py)
            .is_some_and(|(sx, sy)| valid_face_uv(sprite, sx, sy).is_some())
            && near_kaomoji(sprite, projection, (px, py), face)
    }

    /// Screen neighbour shows no Mon; neighbours before the origin are cropped.
    fn empty(
        sprite: &Sprite,
        projection: Projection,
        px: u32,
        py: u32,
        (ox, oy): (i32, i32),
    ) -> bool {
        match (px.checked_add_signed(ox), py.checked_add_signed(oy)) {
            (Some(x), Some(y)) => projection.sample(sprite, x, y).is_none(),
            _ => false,
        }
    }

    fn lit_of(
        sprite: &Sprite,
        colors: &Colors,
        projection: Projection,
        px: u32,
        py: u32,
    ) -> Option<Color> {
        let light = colors.lighting.as_ref()?;
        let (sx, sy) = projection.source_pixel(sprite, px, py)?;
        let (normal, occlusion) = sprite.surface_shading(sx, sy)?;
        let material = sprite.pixel(sx, sy, 1)?;
        Some(shade_terminal_color(
            light,
            colors.ink(usize::from(material)),
            light.brightness(normal, occlusion, false),
        ))
    }

    fn first(cell: &ratatui::buffer::Cell) -> char {
        cell.symbol().chars().next().unwrap_or(' ')
    }

    /// A1: full identity-bearing cells outside the quiet face zone carry an
    /// ASCII glyph in every renderer; colour ASCII fills every voxel cell
    /// with the voxel's own lit colour (no more room background through it).
    #[test]
    fn full_voxels_carry_ascii_shading_in_every_renderer() {
        let config = Config::sample().unwrap();
        let mut worst = (f64::MAX, String::new());
        for depth in [Depth::Rgb, Depth::Indexed] {
            let colors = Colors::from_config(&config, depth).unwrap();
            for shape in [0, 1, 2, 5] {
                for stage in [Stage::Baby, Stage::Adult] {
                    for style in [SpriteStyle::SixteenBit, SpriteStyle::EightBit] {
                        let sprite = mon(stage, style, 45, shape);
                        for size in [(130, 29), (59, 49)] {
                            for framing in [
                                (CameraView::Pet, 100),
                                (CameraView::Pet, 200),
                                (CameraView::Face, 200),
                            ] {
                                for renderer in RENDERERS {
                                    let label = format!(
                                        "{depth:?} shape {shape} {stage:?} {style:?} {size:?} {framing:?} {renderer:?}"
                                    );
                                    let (projection, buf, _, face) =
                                        project(&sprite, renderer, &colors, framing, size);
                                    let (mut full, mut glyphed) = (0u32, 0u32);
                                    for row in 0..size.1 {
                                        for col in 0..size.0 {
                                            let cell = &buf[(col, row)];
                                            let mut all = true;
                                            let mut calm = true;
                                            for (_, px, py) in samples(renderer, col, row) {
                                                all &=
                                                    identity(&sprite, projection, px, py).is_some();
                                                calm &= !quiet(&sprite, projection, face, px, py);
                                            }
                                            if renderer == Renderer::Ascii {
                                                if let Some(lit) = lit_of(
                                                    &sprite,
                                                    &colors,
                                                    projection,
                                                    u32::from(col),
                                                    u32::from(row),
                                                ) {
                                                    assert_eq!(
                                                        cell.bg, lit,
                                                        "{label}: ASCII voxel at {col},{row} keeps its lit colour as background"
                                                    );
                                                }
                                            }
                                            if all && calm {
                                                full += 1;
                                                glyphed +=
                                                    u32::from(first(cell).is_ascii_graphic());
                                            }
                                        }
                                    }
                                    if full >= 12 {
                                        let share = f64::from(glyphed) / f64::from(full);
                                        if share < worst.0 {
                                            worst = (share, label.clone());
                                        }
                                        assert!(
                                            share >= 0.9,
                                            "{label}: only {glyphed} of {full} full voxel cells carry ASCII"
                                        );
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        println!(
            "A1 worst ASCII share of full voxel cells: {:.3} ({})",
            worst.0, worst.1
        );
    }

    /// The nominal ink of each partial-cell ASCII glyph, written independently
    /// of the renderer's table: (glyph, renderer, sample mask).
    fn nominal(renderer: Renderer, glyph: char) -> Vec<u8> {
        match (renderer, glyph) {
            (Renderer::HalfBlock, '_' | '^') => vec![0b10],
            (Renderer::Quadrant, '`') => vec![0b0001],
            (Renderer::Quadrant, '\'') => vec![0b0010],
            (Renderer::Quadrant, ',') => vec![0b0100],
            (Renderer::Quadrant, '.') => vec![0b1000],
            (Renderer::Quadrant, '_') => vec![0b1100],
            (Renderer::Quadrant, '|') => vec![0b0101, 0b1010],
            (Renderer::Quadrant, '\\') => vec![0b1001],
            (Renderer::Quadrant, '/') => vec![0b0110],
            (Renderer::Braille, '_') => vec![0b1100_0000],
            (Renderer::Braille, '.') => vec![0b0100_0000, 0b1000_0000],
            (Renderer::Braille, '-') => vec![0b0000_1100, 0b0011_0000],
            (Renderer::Braille, '|') => vec![0b0101_0101, 0b1010_1010],
            (Renderer::Braille, '/') => vec![0b0101_1010],
            (Renderer::Braille, '\\') => vec![0b1010_0101],
            (Renderer::Braille, '(') => vec![0b1001_0110],
            (Renderer::Braille, ')') => vec![0b0110_1001],
            (Renderer::Braille, '`') => vec![0b0000_0001],
            (Renderer::Braille, '\'') => vec![0b0000_0010],
            (Renderer::Braille, '^') => vec![0b0000_0011],
            _ => Vec::new(),
        }
    }

    /// A2: every silhouette cell is outlined at every size: a partial cell
    /// keeps the scene and draws the Mon's samples in outline ink as a glyph
    /// that re-expresses its mask (or the exact block); a cell with a whole
    /// exposed side is an oriented outline glyph. ASCII outline glyphs agree
    /// with the silhouette's outward normal.
    #[test]
    fn every_mon_has_oriented_ascii_outlines_at_every_size() {
        let config = Config::sample().unwrap();
        let colors = Colors::from_config(&config, Depth::Rgb).unwrap();
        let light = colors.lighting.clone().unwrap();
        let minimum = outline_separation(&light);
        let (mut oriented, mut judged) = (0u32, 0u32);
        for shape in 0..16 {
            for stage in [Stage::Baby, Stage::Adult] {
                for style in [SpriteStyle::SixteenBit, SpriteStyle::EightBit] {
                    for yaw in [0, 45] {
                        let sprite = mon(stage, style, yaw, shape);
                        for size in [(12, 6), (20, 8), (24, 10), (59, 49), (130, 29)] {
                            for framing in [(CameraView::Pet, 100), (CameraView::Face, 100)] {
                                for renderer in RENDERERS {
                                    let label = format!(
                                        "shape {shape} {stage:?} {style:?} yaw {yaw} {size:?} {framing:?} {renderer:?}"
                                    );
                                    let (projection, buf, scene, _) =
                                        project(&sprite, renderer, &colors, framing, size);
                                    let (dx, dy) = renderer.packing();
                                    let count = usize::from(dx * dy);
                                    for row in 0..size.1 {
                                        for col in 0..size.0 {
                                            let cell = &buf[(col, row)];
                                            let mut mask = 0u8;
                                            let mut tagged = true;
                                            for (i, px, py) in samples(renderer, col, row) {
                                                if projection.sample(&sprite, px, py).is_some() {
                                                    mask |= 1 << i;
                                                    tagged &= identity(&sprite, projection, px, py)
                                                        .is_some();
                                                }
                                            }
                                            if mask == 0 || !tagged {
                                                continue;
                                            }
                                            let glyph = first(cell);
                                            if (mask.count_ones() as usize) < count {
                                                let bits: [bool; 8] =
                                                    std::array::from_fn(|i| mask & (1 << i) != 0);
                                                let block = match renderer {
                                                    Renderer::HalfBlock => {
                                                        if mask == 1 {
                                                            '▀'
                                                        } else {
                                                            '▄'
                                                        }
                                                    }
                                                    Renderer::Quadrant => {
                                                        quadrant(std::array::from_fn(|i| bits[i]))
                                                    }
                                                    _ => braille(bits),
                                                };
                                                let tolerance = if renderer == Renderer::Braille {
                                                    1
                                                } else {
                                                    0
                                                };
                                                let ascii_ok = mask.count_ones() as usize * 2
                                                    <= count
                                                    && nominal(renderer, glyph).iter().any(|m| {
                                                        (m ^ mask).count_ones() <= tolerance
                                                    });
                                                assert!(
                                                    glyph == block || ascii_ok,
                                                    "{label}: partial {col},{row} mask {mask:#010b} drew {glyph:?}"
                                                );
                                                assert_eq!(
                                                    cell.bg, scene,
                                                    "{label}: partial {col},{row} keeps the scene"
                                                );
                                                assert!(
                                                    separation(cell.fg, scene) >= minimum,
                                                    "{label}: partial {col},{row} outline ink {:?} on scene {scene:?}",
                                                    cell.fg
                                                );
                                                continue;
                                            }
                                            // Full cell: a whole side exposed to the scene.
                                            let side =
                                                |offset: (i32, i32), on: &dyn Fn(usize) -> bool| {
                                                    samples(renderer, col, row)
                                                        .filter(|(i, _, _)| on(*i))
                                                        .all(|(_, px, py)| {
                                                            empty(
                                                                &sprite, projection, px, py, offset,
                                                            )
                                                        })
                                                };
                                            let (w, h) = (usize::from(dx), usize::from(dy));
                                            let exposed = side((0, -1), &|i| i / w == 0)
                                                || side((1, 0), &|i| i % w == w - 1)
                                                || side((0, 1), &|i| i / w == h - 1)
                                                || side((-1, 0), &|i| i % w == 0);
                                            if !exposed {
                                                continue;
                                            }
                                            assert!(
                                                OUTLINE.contains(glyph),
                                                "{label}: rim {col},{row} drew {glyph:?}"
                                            );
                                            assert!(
                                                separation(cell.fg, cell.bg) >= minimum,
                                                "{label}: rim {col},{row} ink {:?} on {:?}",
                                                cell.fg,
                                                cell.bg
                                            );
                                            if renderer != Renderer::Ascii {
                                                continue;
                                            }
                                            // Outward normal from the empty neighbours.
                                            let (px, py) = (u32::from(col), u32::from(row));
                                            let mut v = [0.0f64; 2];
                                            for (ox, oy) in [
                                                (0, -1),
                                                (1, 0),
                                                (0, 1),
                                                (-1, 0),
                                                (1, -1),
                                                (1, 1),
                                                (-1, 1),
                                                (-1, -1),
                                            ] {
                                                if empty(&sprite, projection, px, py, (ox, oy)) {
                                                    let n = f64::from(ox * ox + oy * oy).sqrt();
                                                    v[0] += f64::from(ox) / n;
                                                    v[1] += f64::from(oy) / n;
                                                }
                                            }
                                            let length = (v[0] * v[0] + v[1] * v[1]).sqrt();
                                            judged += 1;
                                            let r = std::f64::consts::FRAC_1_SQRT_2;
                                            let (axis, signed) = match glyph {
                                                '-' | '_' => ([0.0, 1.0], false),
                                                '|' => ([1.0, 0.0], false),
                                                '/' => ([r, r], false),
                                                '\\' => ([r, -r], false),
                                                '^' => ([0.0, -1.0], true),
                                                'v' => ([0.0, 1.0], true),
                                                '<' => ([-1.0, 0.0], true),
                                                '>' => ([1.0, 0.0], true),
                                                _ => ([0.0, 0.0], false),
                                            };
                                            if length < 1e-9 || glyph == '.' {
                                                oriented += 1;
                                                continue;
                                            }
                                            let dot = (v[0] * axis[0] + v[1] * axis[1]) / length;
                                            let dot = if signed { dot } else { dot.abs() };
                                            oriented += u32::from(dot >= r - 1e-6);
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        let share = f64::from(oriented) / f64::from(judged.max(1));
        println!("A2 ASCII outline orientation agreement: {oriented}/{judged} = {share:.3}");
        assert!(
            judged > 1000,
            "the matrix must judge many ASCII outline cells"
        );
        assert!(
            share >= 0.85,
            "outline orientation agrees on only {share:.3}"
        );
    }

    /// A3: the shading ramp spans the whole configured light range and is
    /// monotonic in every renderer and colour depth; outlines never shade.
    #[test]
    fn ramp_is_monotonic_over_the_full_configured_light_range() {
        let config = Config::sample().unwrap();
        let light = config.voxel_lighting.clone();
        let (ramp, len) = shading_ramp(&light);
        let ramp = &ramp[..len];
        assert!(len >= 2 && ramp.iter().all(|b| !LINE_GLYPHS.contains(b)));
        // Normals swept from facing the light to facing away, times occlusion.
        let toward = light.light_direction.map(f32::from);
        let away = toward.map(|v| -v);
        let mut sweep = Vec::new();
        for step in 0..=24 {
            let t = step as f32 / 24.0;
            let normal: [f32; 3] =
                std::array::from_fn(|i| toward[i] * (1.0 - t) + away[i] * t + 0.01);
            for occlusion in [0u8, 25, 50, 75, 100] {
                sweep.push((normal, occlusion));
            }
        }
        for depth in [Depth::Rgb, Depth::Indexed, Depth::Ansi, Depth::Mono] {
            let colors = Colors::from_config(&config, depth).unwrap();
            let mono = depth == Depth::Mono;
            for renderer in RENDERERS {
                let (dx, dy) = renderer.packing();
                let count = usize::from(dx * dy);
                let mut seen: Vec<(u16, usize)> = Vec::new();
                for &(normal, occlusion) in &sweep {
                    let facet = normal
                        .map(|v| (v / 100.0).clamp(-1.0, 1.0))
                        .map(|v| (v * 127.0) as i8);
                    let overlay = OverlaySample {
                        voxel: [0, 0, 0],
                        facet,
                        material: 8,
                        depth: 0.0,
                        voxel_span: [f32::from(dx), f32::from(dy)],
                        outside: 0,
                        contour: 0,
                        deep_contour: 0,
                        crease: 0,
                        seam: 0,
                        neighbours: [(facet, 8); 4],
                        face_plane: false,
                    };
                    let pixels: [Option<u8>; 8] = std::array::from_fn(|i| (i < count).then_some(8));
                    let surface: [Option<SurfaceLight>; 8] = std::array::from_fn(|i| {
                        (i < count).then_some(SurfaceLight {
                            normal,
                            occlusion,
                            edge: false,
                            screen_edge: false,
                            overlay: Some(overlay),
                        })
                    });
                    let mut cell = ratatui::buffer::Cell::default();
                    paint_lit_pixels(renderer, &colors, pixels, surface, [false; 8], &mut cell);
                    let glyph = first(&cell) as u8;
                    assert!(
                        !LINE_GLYPHS.contains(&glyph),
                        "{depth:?} {renderer:?}: line glyph {:?} used as shading",
                        char::from(glyph)
                    );
                    let index = ramp.iter().position(|b| *b == glyph).unwrap_or_else(|| {
                        panic!(
                            "{depth:?} {renderer:?}: {:?} is not a ramp glyph",
                            char::from(glyph)
                        )
                    });
                    let brightness = light.brightness(normal, occlusion, false);
                    if !mono {
                        assert_eq!(
                            cell.bg,
                            shade_terminal_color(&light, colors.ink(8), brightness),
                            "{depth:?} {renderer:?}: the lit voxel is the background"
                        );
                    }
                    seen.push((brightness, index));
                }
                seen.sort_unstable();
                for pair in seen.windows(2) {
                    let ((b0, i0), (b1, i1)) = (pair[0], pair[1]);
                    // Colour: denser is darker. Mono: denser is brighter.
                    let monotone = if mono { i1 >= i0 } else { i1 <= i0 };
                    assert!(
                        b0 == b1 || monotone,
                        "{depth:?} {renderer:?}: brightness {b0}->{b1} moved the ramp {i0}->{i1}"
                    );
                }
                let mut used: Vec<usize> = seen.iter().map(|(_, i)| *i).collect();
                used.sort_unstable();
                used.dedup();
                assert!(
                    used.len() * 5 >= len * 4,
                    "{depth:?} {renderer:?}: the light range uses only {} of {len} ramp glyphs",
                    used.len()
                );
            }
        }
    }

    /// A4: a glyph belongs to its voxel. Over a fine orbit, cells of a flat
    /// surface that show the same voxels' same faces in the same light band
    /// keep their glyph (view-space normals turn with the camera, so a face
    /// is matched by a normal within about 25 degrees).
    #[test]
    fn overlay_glyphs_follow_voxels_through_orbit() {
        use std::collections::HashMap;
        let config = Config::sample().unwrap();
        let colors = Colors::from_config(&config, Depth::Rgb).unwrap();
        let light = colors.lighting.clone().unwrap();
        let (_, len) = shading_ramp(&light);
        type Key = Vec<([i32; 3], usize)>;
        type Faces = Vec<[f32; 3]>;
        type Tally = Vec<(Faces, HashMap<char, u32>)>;
        let (mut kept_total, mut shared_total) = (0u32, 0u32);
        let mut worst = 1.0f64;
        for renderer in [Renderer::Ascii, Renderer::HalfBlock, Renderer::Quadrant] {
            for (shape, framing) in [
                (0, (CameraView::Pet, 200)),
                (1, (CameraView::Pet, 200)),
                (1, (CameraView::Face, 200)),
            ] {
                let yaws: Vec<u16> = (40..=50).chain((0..=90).step_by(5)).collect();
                let (dx, dy) = renderer.packing();
                let frames: Vec<HashMap<Key, Vec<(Faces, char)>>> = yaws
                    .iter()
                    .map(|&yaw| {
                        let sprite = mon(Stage::Baby, SpriteStyle::SixteenBit, yaw, shape);
                        let (projection, buf, _, face) =
                            project(&sprite, renderer, &colors, framing, (130, 29));
                        // Ground-truth flat interior: each sample's four
                        // screen neighbours show the same flat surface (same
                        // normal and material, lattice-adjacent voxels).
                        let flat = |px: u32, py: u32| {
                            let Some((voxel, facet)) = identity(&sprite, projection, px, py) else {
                                return false;
                            };
                            let material = projection.sample(&sprite, px, py);
                            !quiet(&sprite, projection, face, px, py)
                                && [(0, -1), (1, 0), (0, 1), (-1, 0)]
                                    .into_iter()
                                    .all(|(ox, oy)| {
                                        let (Some(nx), Some(ny)) =
                                            (px.checked_add_signed(ox), py.checked_add_signed(oy))
                                        else {
                                            return false;
                                        };
                                        !quiet(&sprite, projection, face, nx, ny)
                                            && identity(&sprite, projection, nx, ny).is_some_and(
                                                |(other, other_facet)| {
                                                    other_facet == facet
                                                        && projection.sample(&sprite, nx, ny)
                                                            == material
                                                        && (0..3).all(|i| {
                                                            (voxel[i] - other[i]).abs() <= 2
                                                        })
                                                },
                                            )
                                    })
                        };
                        let mut votes: HashMap<Key, Tally> = HashMap::new();
                        for row in 0..29 {
                            for col in 0..130 {
                                if !samples(renderer, col, row).all(|(_, px, py)| flat(px, py)) {
                                    continue;
                                }
                                let mut key = Key::new();
                                let mut faces = Faces::new();
                                let mut tone = 0.0;
                                for (_, px, py) in samples(renderer, col, row) {
                                    let (sx, sy) =
                                        projection.source_pixel(&sprite, px, py).unwrap();
                                    let (normal, occlusion) =
                                        sprite.surface_shading(sx, sy).unwrap();
                                    let level = light_level(
                                        &light,
                                        light.brightness(normal, occlusion, false),
                                    );
                                    let band = ((1.0 - level) * (len - 1) as f32).round() as usize;
                                    tone += (1.0 - level) / f32::from(dx * dy);
                                    key.push((sprite.surface_voxel(sx, sy).unwrap(), band));
                                    faces.push(normal);
                                }
                                // The band of the cell's mean light, too: a packed
                                // cell shades its samples' mean.
                                key.push(([0; 3], (tone * (len - 1) as f32).round() as usize));
                                let glyph = first(&buf[(col, row)]);
                                let entries = votes.entry(key).or_default();
                                let same = |a: &Faces, b: &Faces| {
                                    a.iter()
                                        .zip(b)
                                        .all(|(m, n)| m[0] * n[0] + m[1] * n[1] + m[2] * n[2] > 0.9)
                                };
                                match entries.iter_mut().find(|(known, _)| same(known, &faces)) {
                                    Some((_, tally)) => *tally.entry(glyph).or_default() += 1,
                                    None => entries.push((faces, HashMap::from([(glyph, 1)]))),
                                }
                            }
                        }
                        votes
                            .into_iter()
                            .map(|(key, entries)| {
                                let entries = entries
                                    .into_iter()
                                    .map(|(faces, tally)| {
                                        let glyph = tally
                                            .into_iter()
                                            .max_by_key(|(g, n)| (*n, *g))
                                            .map(|(g, _)| g)
                                            .unwrap();
                                        (faces, glyph)
                                    })
                                    .collect();
                                (key, entries)
                            })
                            .collect()
                    })
                    .collect();
                for (step, pair) in frames.windows(2).enumerate() {
                    if yaws[step + 1] < yaws[step] {
                        continue;
                    }
                    let (mut kept, mut shared) = (0u32, 0u32);
                    for (key, entries) in &pair[1] {
                        let Some(before) = pair[0].get(key) else {
                            continue;
                        };
                        for (faces, glyph) in entries {
                            let earlier = before.iter().find(|(known, _)| {
                                known
                                    .iter()
                                    .zip(faces)
                                    .all(|(m, n)| m[0] * n[0] + m[1] * n[1] + m[2] * n[2] > 0.9)
                            });
                            if let Some((_, previous)) = earlier {
                                shared += 1;
                                kept += u32::from(previous == glyph);
                            }
                        }
                    }
                    kept_total += kept;
                    shared_total += shared;
                    if shared >= 20 {
                        let share = f64::from(kept) / f64::from(shared);
                        worst = worst.min(share);
                        assert!(
                            share >= 0.9,
                            "{renderer:?} shape {shape} {framing:?} yaw {}->{}: {kept} of {shared} persisting voxel faces kept their glyph",
                            yaws[step],
                            yaws[step + 1]
                        );
                    }
                }
            }
        }
        println!("A4 glyph persistence: {kept_total}/{shared_total}; worst step {worst:.3}");
        assert!(
            shared_total >= 500,
            "the orbit must judge many persisting faces ({shared_total})"
        );
    }

    /// A5: creases (a change of face orientation on one continuous surface)
    /// draw a line on the darker face when magnified, and stay quiet when the
    /// Mon is minified below one cell per voxel.
    #[test]
    fn creases_appear_when_magnified_and_stay_quiet_when_minified() {
        let config = Config::sample().unwrap();
        let colors = Colors::from_config(&config, Depth::Rgb).unwrap();
        let light = colors.lighting.clone().unwrap();
        let diffuse = |n: [i8; 3]| light.brightness(n.map(|v| f32::from(v) / 127.0), 0, false);
        let (mut magnified, mut drawn) = (0u32, 0u32);
        let (mut calm, mut noisy) = (0u32, 0u32);
        for shape in [0, 1, 2] {
            let sprite = mon(Stage::Baby, SpriteStyle::SixteenBit, 45, shape);
            let extent = sprite.voxel_extent_pixels().unwrap();
            for (framing, size, renderer) in [
                ((CameraView::Face, 200), (130, 29), Renderer::Ascii),
                ((CameraView::Face, 400), (130, 29), Renderer::Ascii),
                ((CameraView::Pet, 400), (130, 29), Renderer::Ascii),
                ((CameraView::Face, 400), (59, 49), Renderer::Ascii),
                ((CameraView::Pet, 100), (24, 10), Renderer::Ascii),
                ((CameraView::Pet, 50), (59, 49), Renderer::Ascii),
                ((CameraView::Pet, 100), (20, 8), Renderer::HalfBlock),
                ((CameraView::Pet, 50), (130, 29), Renderer::HalfBlock),
            ] {
                let (projection, buf, _, face) = project(&sprite, renderer, &colors, framing, size);
                let (dx, dy) = renderer.packing();
                let cells_per_voxel = (projection.scale * extent / f64::from(dx))
                    .min(projection.scale_y * extent / f64::from(dy));
                let tolerance = [projection.scale, projection.scale_y]
                    .map(|s| 1 + (1.0 / (s * extent)).ceil() as i32);
                for row in 0..size.1 {
                    for col in 0..size.0 {
                        let glyph = first(&buf[(col, row)]);
                        let mut crease = false;
                        let mut continuous = true;
                        let mut skip = false;
                        for (_, px, py) in samples(renderer, col, row) {
                            let Some((voxel, facet)) = identity(&sprite, projection, px, py) else {
                                skip = true;
                                continue;
                            };
                            skip |= quiet(&sprite, projection, face, px, py);
                            let depth = projection
                                .source_pixel(&sprite, px, py)
                                .and_then(|(sx, sy)| sprite.depth_pixels(sx, sy))
                                .unwrap_or(0.0);
                            for (axis, (ox, oy)) in
                                [(1usize, (0, -1)), (0, (1, 0)), (1, (0, 1)), (0, (-1, 0))]
                            {
                                if empty(&sprite, projection, px, py, (ox, oy)) {
                                    skip = true;
                                    continue;
                                }
                                let (Some(nx), Some(ny)) =
                                    (px.checked_add_signed(ox), py.checked_add_signed(oy))
                                else {
                                    continue;
                                };
                                let Some((other, other_facet)) =
                                    identity(&sprite, projection, nx, ny)
                                else {
                                    continue;
                                };
                                let lattice = (0..3)
                                    .map(|i| (voxel[i] - other[i]).abs())
                                    .max()
                                    .unwrap_or(0);
                                if lattice > tolerance[axis] {
                                    continuous = false;
                                    continue;
                                }
                                let other_depth = projection
                                    .source_pixel(&sprite, nx, ny)
                                    .and_then(|(sx, sy)| sprite.depth_pixels(sx, sy))
                                    .unwrap_or(0.0);
                                let darker = diffuse(facet) < diffuse(other_facet)
                                    || (diffuse(facet) == diffuse(other_facet)
                                        && facet < other_facet);
                                if lattice <= 1
                                    && other_facet != facet
                                    && darker
                                    && (depth - other_depth).abs() <= 2.0 * extent
                                {
                                    crease = true;
                                }
                            }
                        }
                        if skip || !continuous {
                            continue;
                        }
                        if cells_per_voxel >= f64::from(CREASE_BAND.1) {
                            if crease {
                                magnified += 1;
                                drawn += u32::from(LINES.contains(glyph));
                            }
                        } else if cells_per_voxel < f64::from(CREASE_BAND.0) {
                            calm += 1;
                            noisy += u32::from(LINES.contains(glyph));
                        }
                    }
                }
            }
        }
        let shown = f64::from(drawn) / f64::from(magnified.max(1));
        let noise = f64::from(noisy) / f64::from(calm.max(1));
        println!("A5 magnified creases drawn {drawn}/{magnified} = {shown:.3}; minified crease noise {noisy}/{calm} = {noise:.3}");
        assert!(
            magnified >= 50 && calm >= 50,
            "the matrix must judge both bands ({magnified}, {calm})"
        );
        assert!(
            shown >= 0.8,
            "only {shown:.3} of magnified creases carry a crease glyph"
        );
        assert!(
            noise <= 0.05,
            "minified interiors carry crease glyphs on {noise:.3}"
        );
    }

    /// The overlay never alters facial flat ink, never paints a world-only
    /// cell, keeps the world surface as a mixed cell's background, leaves
    /// every face-pass glyph unchanged and keeps shading off the face zone.
    #[test]
    fn overlay_never_touches_face_ink_the_world_floor_or_the_kaomoji() {
        let config = Config::sample().unwrap();
        let colors = Colors::from_config(&config, Depth::Rgb).unwrap();
        let light = colors.lighting.clone().unwrap();
        // (a) Flat ink: identical with and without identity, every packing.
        for renderer in RENDERERS {
            let (dx, dy) = renderer.packing();
            let count = usize::from(dx * dy);
            for ink_mask in 1..(1u16 << count) {
                let overlay = OverlaySample {
                    voxel: [1, 2, 3],
                    facet: [0, 0, 127],
                    material: 8,
                    depth: 1.0,
                    voxel_span: [4.0; 2],
                    outside: DIR_N | DIR_W,
                    contour: DIR_E,
                    deep_contour: 0,
                    crease: DIR_S,
                    seam: 0,
                    neighbours: [([0, 127, 0], 8); 4],
                    face_plane: false,
                };
                let ink: [bool; 8] = std::array::from_fn(|i| i < count && ink_mask & (1 << i) != 0);
                let pixels: [Option<u8>; 8] =
                    std::array::from_fn(|i| (i < count).then_some(if ink[i] { 0 } else { 8 }));
                let with = |overlay: Option<OverlaySample>| {
                    let surface: [Option<SurfaceLight>; 8] = std::array::from_fn(|i| {
                        (i < count).then_some(SurfaceLight {
                            normal: [0.0, 0.0, 1.0],
                            occlusion: 0,
                            edge: false,
                            screen_edge: false,
                            overlay,
                        })
                    });
                    let mut cell = ratatui::buffer::Cell::default();
                    paint_lit_pixels(renderer, &colors, pixels, surface, ink, &mut cell);
                    cell
                };
                assert_eq!(
                    with(Some(overlay)),
                    with(None),
                    "{renderer:?}: face ink {ink_mask:#b} was overpainted"
                );
            }
        }
        // (b) World mixed cells: the world sample's own lit colour stays the
        // background; world-only cells keep their exact legacy packing.
        for renderer in [Renderer::HalfBlock, Renderer::Quadrant, Renderer::Braille] {
            let (dx, dy) = renderer.packing();
            let count = usize::from(dx * dy);
            let world_normal = [0.0, 1.0, 0.0];
            let world = shade_terminal_color(
                &light,
                colors.ink(14),
                light.brightness(world_normal, 0, false),
            );
            for pet_mask in 0..(1u16 << count) {
                let pixels: [Option<u8>; 8] = std::array::from_fn(|i| {
                    (i < count).then_some(if pet_mask & (1 << i) != 0 { 8 } else { 14 })
                });
                let surface = |overlay: bool| -> [Option<SurfaceLight>; 8] {
                    std::array::from_fn(|i| {
                        let pet = i < count && pet_mask & (1 << i) != 0;
                        (i < count).then_some(SurfaceLight {
                            normal: if pet { [0.0, 0.0, 1.0] } else { world_normal },
                            occlusion: 0,
                            edge: false,
                            screen_edge: false,
                            overlay: (pet && overlay).then_some(OverlaySample {
                                voxel: [0, i as i32, 0],
                                facet: [0, 0, 127],
                                material: 8,
                                depth: 0.0,
                                voxel_span: [1.0; 2],
                                outside: 0,
                                contour: 0,
                                deep_contour: 0,
                                crease: 0,
                                seam: 0,
                                neighbours: [([0, 0, 127], 8); 4],
                                face_plane: false,
                            }),
                        })
                    })
                };
                let paint = |overlay: bool| {
                    let mut cell = ratatui::buffer::Cell::default();
                    cell.set_bg(colors.ink(6));
                    paint_lit_pixels(
                        renderer,
                        &colors,
                        pixels,
                        surface(overlay),
                        [false; 8],
                        &mut cell,
                    );
                    cell
                };
                let cell = paint(true);
                if pet_mask == 0 {
                    assert_eq!(
                        cell,
                        paint(false),
                        "{renderer:?}: a world-only cell changed"
                    );
                } else if (pet_mask.count_ones() as usize) < count {
                    assert_eq!(
                        cell.bg, world,
                        "{renderer:?} pet {pet_mask:#b}: the floor behind the pet was erased"
                    );
                    assert!(separation(cell.fg, world) >= outline_separation(&light));
                }
            }
        }
        // (b') A real room: identity changes no cell away from the pet.
        let sprite = mon(Stage::Baby, SpriteStyle::SixteenBit, 45, 1);
        let plain = sprite.without_voxel_identity();
        let tinted = plain.recolored(3, 9);
        let rules = WorldRules::default();
        for renderer in RENDERERS {
            let area = Rect::new(0, 0, 130, 29);
            let room = |sprite: &Sprite| {
                let mut buf = Buffer::empty(area);
                RoomWidget {
                    world: Some(&rules),
                    sprite,
                    detail: SpriteStyle::SixteenBit,
                    renderer,
                    scale: 0,
                    colors: &colors,
                    expression: Expression::Idle,
                    frame: 0,
                    position: (500, 700),
                    camera: Camera {
                        orbit_degrees: 45,
                        ..camera(CameraView::Room, 200)
                    },
                    habitat: None,
                    face_detail: None,
                }
                .render(area, &mut buf);
                buf
            };
            let (on, off, recoloured) = (room(&sprite), room(&plain), room(&tinted));
            let pet: Vec<(u16, u16)> = (0..29)
                .flat_map(|y| (0..130).map(move |x| (x, y)))
                .filter(|&p| off[p] != recoloured[p])
                .collect();
            assert!(
                !pet.is_empty(),
                "{renderer:?}: the pet must be visible in the room"
            );
            // The pet's bounding box (from its recoloured materials), plus a
            // two-cell margin for materials recolouring cannot reveal.
            let x0 = pet.iter().map(|p| p.0).min().unwrap();
            let x1 = pet.iter().map(|p| p.0).max().unwrap();
            let y0 = pet.iter().map(|p| p.1).min().unwrap();
            let y1 = pet.iter().map(|p| p.1).max().unwrap();
            let near_pet =
                |(x, y): (u16, u16)| x + 2 >= x0 && x <= x1 + 2 && y + 2 >= y0 && y <= y1 + 2;
            for y in 0..29 {
                for x in 0..130 {
                    if !near_pet((x, y)) {
                        assert_eq!(
                            on[(x, y)],
                            off[(x, y)],
                            "{renderer:?}: world cell {x},{y} changed"
                        );
                    }
                }
            }
        }
        // (c) The kaomoji: every face-pass cell keeps its glyph and ink, and
        // the quiet face zone carries no shading glyph.
        let (ramp, len) = shading_ramp(&light);
        let shading: Vec<char> = ramp[..len]
            .iter()
            .map(|b| char::from(*b))
            .filter(|c| *c != '.')
            .collect();
        for shape in [0, 1, 2] {
            let sprite = Sprite::voxel_with_appearance(
                Stage::Baby,
                Expression::Happy,
                SpriteStyle::SixteenBit,
                0,
                20,
                false,
                Appearance {
                    shape,
                    body: shape,
                    accent: (shape + 5) % 16,
                },
            );
            let plain = sprite.without_voxel_identity();
            for renderer in RENDERERS {
                for framing in [
                    (CameraView::Face, 100),
                    (CameraView::Face, 200),
                    (CameraView::Face, 400),
                    (CameraView::Pet, 100),
                ] {
                    let label = format!("shape {shape} {renderer:?} {framing:?}");
                    let size = (130, 29);
                    let render_face = |sprite: &Sprite| {
                        let (projection, mut buf, _, face) =
                            project(sprite, renderer, &colors, framing, size);
                        let before = buf.clone();
                        draw_camera_face(sprite, renderer, &colors, projection, buf.area, &mut buf);
                        (projection, before, buf, face)
                    };
                    let (projection, _, on, face) = render_face(&sprite);
                    let (_, before, off, _) = render_face(&plain);
                    let mut drawn = 0;
                    for y in 0..size.1 {
                        for x in 0..size.0 {
                            drawn += usize::from(before[(x, y)] != off[(x, y)]);
                            // A face-pass glyph: the legacy face pass replaced the
                            // body glyph with a character (native, stroke or atlas
                            // mark). Atlas ink pixels are covered by (a).
                            if before[(x, y)].symbol() != off[(x, y)].symbol()
                                && first(&off[(x, y)]).is_ascii_graphic()
                            {
                                assert_eq!(
                                    on[(x, y)].symbol(),
                                    off[(x, y)].symbol(),
                                    "{label}: face glyph at {x},{y}"
                                );
                                assert_eq!(
                                    on[(x, y)].fg,
                                    off[(x, y)].fg,
                                    "{label}: face ink at {x},{y}"
                                );
                                continue;
                            }
                            let all_quiet = samples(renderer, x, y)
                                .all(|(_, px, py)| quiet(&sprite, projection, face, px, py));
                            if all_quiet {
                                assert!(
                                    !shading.contains(&first(&on[(x, y)])),
                                    "{label}: shading glyph {:?} in the quiet face zone at {x},{y}",
                                    on[(x, y)].symbol()
                                );
                            }
                        }
                    }
                    assert!(
                        drawn > 0 || framing.0 == CameraView::Pet,
                        "{label}: the face must draw"
                    );
                }
            }
        }
    }

    /// Lighting disabled or absent, and classic pixel sprites, keep the exact
    /// legacy packing; monochrome overlay cells carry glyphs only.
    #[test]
    fn disabled_lighting_pixel_sprites_and_mono_stay_coherent() {
        let config = Config::sample().unwrap();
        let sprite = mon(Stage::Adult, SpriteStyle::SixteenBit, 45, 1);
        let plain = sprite.without_voxel_identity();
        let pixel_art =
            Sprite::animated(Stage::Child, Expression::Happy, SpriteStyle::SixteenBit, 0);
        assert!(pixel_art.voxel_extent_pixels().is_none());
        for depth in [Depth::Rgb, Depth::Indexed, Depth::Ansi, Depth::Mono] {
            let mut colors = Colors::from_config(&config, depth).unwrap();
            for renderer in RENDERERS {
                for framing in [(CameraView::Pet, 100), (CameraView::Face, 300)] {
                    let enabled = colors.clone();
                    let (_, pixel_on, _, _) =
                        project(&pixel_art, renderer, &enabled, framing, (59, 49));
                    colors.lighting.as_mut().unwrap().enabled = false;
                    let (_, on, _, _) = project(&sprite, renderer, &colors, framing, (59, 49));
                    let (_, off, _, _) = project(&plain, renderer, &colors, framing, (59, 49));
                    assert_eq!(on, off, "{depth:?} {renderer:?} {framing:?}: disabled lighting must keep the legacy packing");
                    let (_, pixel_off, _, _) =
                        project(&pixel_art, renderer, &colors, framing, (59, 49));
                    // (Indexed/ANSI half-block legacy lighting compares
                    // quantized colours, a pre-existing legacy difference.)
                    assert!(
                        !matches!(depth, Depth::Rgb | Depth::Mono) || pixel_on == pixel_off,
                        "{depth:?} {renderer:?}: classic pixel sprites never take the overlay"
                    );
                    let mut none = colors.clone();
                    none.lighting = None;
                    let (_, unlit, _, _) = project(&sprite, renderer, &none, framing, (59, 49));
                    let (_, unlit_plain, _, _) =
                        project(&plain, renderer, &none, framing, (59, 49));
                    assert_eq!(
                        unlit, unlit_plain,
                        "{depth:?} {renderer:?}: no lighting model keeps the legacy packing"
                    );
                    colors.lighting.as_mut().unwrap().enabled = true;
                    if depth == Depth::Mono {
                        let (projection, buf, _, _) =
                            project(&sprite, renderer, &colors, framing, (59, 49));
                        for y in 0..49 {
                            for x in 0..59 {
                                let mon_here = samples(renderer, x, y).any(|(_, px, py)| {
                                    projection.sample(&sprite, px, py).is_some()
                                });
                                if mon_here {
                                    let cell = &buf[(x, y)];
                                    assert_ne!(
                                        cell.symbol(),
                                        " ",
                                        "{renderer:?}: mono Mon cell {x},{y} blanked"
                                    );
                                    assert_eq!(
                                        cell.fg,
                                        Color::Reset,
                                        "{renderer:?}: mono cell {x},{y} coloured"
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

#[cfg(test)]
mod eye_plate_tests {
    //! F1 eye whites: sclera plates behind open-eye kaomoji strokes in every
    //! face tier. Plates are found from the buffer alone (cells the face pass
    //! changed that show the eye white, or reverse video in monochrome) and
    //! judged against the sprite's own head/face UV maps and the projection.
    use super::*;
    use crate::{
        game::Stage,
        sprite::{Appearance, Expression, SpriteStyle},
    };

    const RENDERERS: [Renderer; 4] = [
        Renderer::Ascii,
        Renderer::HalfBlock,
        Renderer::Quadrant,
        Renderer::Braille,
    ];
    /// Root's live fox (seed 513): its face ink is the outline ink, so the eye
    /// white identifies plates unambiguously.
    const FOX: Appearance = Appearance {
        shape: 1,
        body: 8,
        accent: 3,
    };
    /// Watch, restricted, micro, native and live viewports, with the camera
    /// view and zoom that reach every face tier.
    const CASES: [((u16, u16), CameraView, u16); 13] = [
        ((12, 6), CameraView::Pet, 100),
        ((20, 8), CameraView::Pet, 100),
        ((16, 3), CameraView::Pet, 100),
        ((24, 10), CameraView::Pet, 200),
        ((24, 10), CameraView::Face, 100),
        ((40, 16), CameraView::Pet, 100),
        ((40, 16), CameraView::Face, 100),
        ((75, 7), CameraView::Face, 100),
        ((75, 7), CameraView::Face, 200),
        ((75, 7), CameraView::Face, 400),
        ((71, 20), CameraView::Face, 200),
        ((71, 20), CameraView::Face, 400),
        ((57, 47), CameraView::Face, 300),
    ];

    fn mon(expression: Expression, frame: usize, yaw: u16, pitch: f32) -> Sprite {
        Sprite::voxel_with_pose(
            Stage::Baby,
            expression,
            SpriteStyle::SixteenBit,
            frame,
            (yaw, pitch),
            false,
            FOX,
        )
    }

    fn camera(view: CameraView, zoom_percent: u16) -> Camera {
        Camera {
            view,
            from_view: view,
            zoom_percent,
            transition_percent: 100,
            ..Camera::default()
        }
    }

    struct Face {
        lod: FaceLod,
        drawn: bool,
        projection: Projection,
        before: Buffer,
        after: Buffer,
    }

    /// The Mon over a room-coloured scene, then the face pass over it.
    fn render_face(
        sprite: &Sprite,
        renderer: Renderer,
        colors: &Colors,
        ((width, height), view, zoom): ((u16, u16), CameraView, u16),
        scene: Color,
    ) -> Face {
        let area = Rect::new(0, 0, width, height);
        let mut before = Buffer::empty(area);
        for cell in &mut before.content {
            cell.set_bg(scene);
        }
        let projection = Projection::camera(
            sprite,
            SolidBounds::of(sprite).unwrap(),
            area,
            renderer,
            0,
            (500, 700),
            camera(view, zoom),
        );
        draw_projection(sprite, renderer, colors, projection, area, &mut before);
        let mut after = before.clone();
        let drawn = draw_camera_face(sprite, renderer, colors, projection, area, &mut after);
        let lod = match (FaceTexture::new(sprite), FaceTexture::bounds(sprite)) {
            (Some(texture), Some(bounds)) if height > 3 => {
                let (l, t) = projection.cell(bounds.left, bounds.top, renderer);
                let (r, b) = projection.cell(bounds.right, bounds.bottom, renderer);
                let fit = FaceFit::of(sprite, renderer, projection, area, texture, [l, t, r, b]);
                let stroke = face_ink(sprite, colors, InkRegion::TextBox);
                let atlas = renderer.packing() != (1, 1)
                    && !(renderer == Renderer::HalfBlock && stroke != colors.ink(0));
                face_lod(texture, (r - l) * fit.shrink, (b - t) * fit.shrink, atlas)
            }
            _ => FaceLod::Native,
        };
        Face {
            lod,
            drawn,
            projection,
            before,
            after,
        }
    }

    /// Cells the face pass changed that show the eye white (reverse video in
    /// monochrome).
    fn plates(face: &Face, sclera: Sclera) -> Vec<(u16, u16)> {
        let area = face.after.area;
        let mut cells = Vec::new();
        for row in area.y..area.bottom() {
            for col in area.x..area.right() {
                let (old, new) = (&face.before[(col, row)], &face.after[(col, row)]);
                let plate = if sclera.mono {
                    new.modifier.contains(ratatui::style::Modifier::REVERSED)
                } else {
                    new.bg == sclera.white || new.fg == sclera.white
                };
                if old != new && plate {
                    cells.push((col, row));
                }
            }
        }
        cells
    }

    /// Mean head-map UV of a cell's packed samples, and whether every sample
    /// is authorized head surface (no nose, occluder, rear or off-head pixel).
    fn head_uv(
        sprite: &Sprite,
        renderer: Renderer,
        projection: Projection,
        (col, row): (u16, u16),
    ) -> (Option<[f64; 2]>, bool) {
        let (dx, dy) = renderer.packing();
        let mut sum = [0.0; 2];
        let mut n = 0.0;
        let mut complete = true;
        for y in 0..dy {
            for x in 0..dx {
                let px = u32::from(col) * u32::from(dx) + u32::from(x);
                let py = u32::from(row) * u32::from(dy) + u32::from(y);
                match projected_head_uv(sprite, projection, px, py) {
                    Some((uv, _)) => {
                        sum[0] += uv[0];
                        sum[1] += uv[1];
                        n += 1.0;
                    }
                    None => complete = false,
                }
            }
        }
        ((n > 0.0).then(|| sum.map(|v| v / n)), complete)
    }

    /// WCAG contrast ratio of two terminal colours.
    fn contrast(a: Color, b: Color) -> f64 {
        let relative = |color: Color| {
            let rgb = terminal_rgb(color).unwrap_or([0; 3]).map(|c| {
                let c = f64::from(c) / 255.0;
                if c <= 0.03928 {
                    c / 12.92
                } else {
                    ((c + 0.055) / 1.055).powf(2.4)
                }
            });
            0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
        };
        let (a, b) = (relative(a), relative(b));
        (a.max(b) + 0.05) / (a.min(b) + 0.05)
    }

    #[test]
    fn sclera_plates_fill_open_eyes_and_never_reach_mouths_or_parentheses() {
        // `o`: the ring, its counter and a one-unit rim; `.`: a 3x3 dot.
        let o = sclera_rows(b'o').unwrap();
        assert_eq!(o[1 + 4] & 0b0111110, 0b0111110, "o's counter is filled");
        assert_eq!(o[1 + 1], 0b0111110, "one-unit rim above the ring");
        assert_eq!(o[1 + 8], 0, "no plate two units below the ring");
        let dot = sclera_rows(b'.').unwrap();
        let bits: u32 = dot.iter().map(|r| r.count_ones()).sum();
        assert_eq!(bits, 9, "{dot:?}");
        assert_eq!(dot[1 + 7], 0b0011100);
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
                let texture = FaceTexture { text, aspect: None };
                for y in -1..=9 {
                    for x in -2..texture.width() as i32 + 2 {
                        if !texture.sclera_bit(x, y) {
                            continue;
                        }
                        let index = (x + 1).div_euclid(8) as usize;
                        assert!(
                            plated_eye(text, index),
                            "{text}: plate at {x},{y} off an open eye"
                        );
                        if texture.bit(x, y) {
                            assert_eq!(x.div_euclid(8) as usize, index, "{text}: {x},{y}");
                        }
                    }
                }
            }
        }
    }

    /// T-E1: every open-eye expression gets a plate on both frontal eyes in
    /// every tier and renderer, the pupil ink clears it by 7:1 and by the
    /// outline separation, and monochrome reverses video.
    #[test]
    fn open_eyes_get_contrasting_plates_at_every_tier() {
        let config = Config::sample().unwrap();
        let open = [
            (Expression::Idle, 0, "(o_o)"),
            (Expression::Surprised, 0, "(O_O)"),
            (Expression::Sick, 1, "(@_@)"),
            (Expression::Sick, 0, "(+_+)"),
            (Expression::Shy, 0, "(._.)"),
            (Expression::Excited, 0, "(*_*)"),
        ];
        let mut tiers = std::collections::BTreeMap::new();
        // Plates draw, flat, with lighting disabled too.
        for (depth, lit) in [
            (Depth::Rgb, true),
            (Depth::Indexed, true),
            (Depth::Mono, true),
            (Depth::Rgb, false),
        ] {
            let mut colors = Colors::from_config(&config, depth).unwrap();
            if let Some(light) = colors.lighting.as_mut() {
                light.enabled = lit;
            }
            let sclera = Sclera::of(&colors);
            if depth != Depth::Mono {
                let light = colors.lighting.as_ref().unwrap();
                assert!(
                    contrast(sclera.ink, sclera.white) >= 7.0,
                    "{depth:?}: {sclera:?}"
                );
                assert!(separation(sclera.ink, sclera.white) >= outline_separation(light));
            }
            for (expression, frame, text) in open {
                let sprite = mon(expression, frame, 0, 0.32);
                assert_eq!(sprite.face_text(), Some(text));
                assert_eq!(
                    face_ink(&sprite, &colors, InkRegion::TextBox),
                    colors.ink(0)
                );
                for renderer in RENDERERS {
                    for case in CASES {
                        let face = render_face(&sprite, renderer, &colors, case, colors.ink(10));
                        if !face.drawn {
                            continue;
                        }
                        let label = format!(
                            "{text} {depth:?} lit {lit} {renderer:?} {case:?} {:?}",
                            face.lod
                        );
                        let cells = plates(&face, sclera);
                        let mut sides = [0; 2];
                        for &pos in &cells {
                            let (uv, _) = head_uv(&sprite, renderer, face.projection, pos);
                            let uv = uv.unwrap_or_else(|| {
                                panic!("{label}: plate at {pos:?} off the head")
                            });
                            sides[usize::from(uv[0] > 0.5)] += 1;
                            let cell = &face.after[pos];
                            let stroke = cell.symbol().chars().next().unwrap_or(' ');
                            if !sclera.mono && cell.bg == sclera.white && stroke.is_ascii_graphic()
                            {
                                assert!(
                                    contrast(cell.fg, cell.bg) >= 7.0,
                                    "{label}: eye ink {:?} on {:?} at {pos:?}",
                                    cell.fg,
                                    cell.bg
                                );
                            }
                        }
                        assert!(sides[0] > 0, "{label}: no sclera plate on the left eye");
                        assert!(sides[1] > 0, "{label}: no sclera plate on the right eye");
                        *tiers
                            .entry((format!("{renderer:?}"), format!("{:?}", face.lod)))
                            .or_insert(0) += 1;
                    }
                }
            }
        }
        for renderer in RENDERERS {
            let name = format!("{renderer:?}");
            let atlas = renderer.packing() != (1, 1);
            for lod in ["Native", "Shaded", "Atlas"] {
                if lod == "Atlas" && !atlas {
                    continue;
                }
                assert!(
                    tiers
                        .get(&(name.clone(), lod.to_string()))
                        .copied()
                        .unwrap_or(0)
                        >= 3,
                    "{name} {lod} faces were not exercised: {tiers:?}"
                );
            }
        }
    }

    /// T-E2: closed, squinting and tick eyes, mouths, rears, profiles and
    /// occluders never get a plate; a wink plates its open eye only.
    #[test]
    fn closed_eyes_mouths_rears_and_occluders_never_get_plates() {
        let config = Config::sample().unwrap();
        let closed = [
            (Expression::Idle, 2, "(-_-)"),
            (Expression::Happy, 0, "(^_^)"),
            (Expression::Hungry, 0, "(u_u)"),
            (Expression::Angry, 0, "(>_<)"),
            (Expression::Dead, 0, "(x_x)"),
            (Expression::Sleeping, 2, "(=_=)"),
            (Expression::Sad, 0, "(T_T)"),
            (Expression::Sad, 1, "(;_;)"),
            (Expression::Angry, 3, "(`_')"),
            (Expression::Shy, 5, "('o')"),
            (Expression::Excited, 5, "(^O^)"),
        ];
        let mut checked = 0;
        for depth in [Depth::Rgb, Depth::Mono] {
            let colors = Colors::from_config(&config, depth).unwrap();
            let sclera = Sclera::of(&colors);
            for renderer in RENDERERS {
                for case in CASES {
                    for (expression, frame, text) in closed {
                        let sprite = mon(expression, frame, 0, 0.32);
                        assert_eq!(sprite.face_text(), Some(text));
                        let face = render_face(&sprite, renderer, &colors, case, colors.ink(10));
                        checked += usize::from(face.drawn);
                        let cells = plates(&face, sclera);
                        assert!(
                            cells.is_empty(),
                            "{text} {depth:?} {renderer:?} {case:?} {:?}: plate on a closed eye at {cells:?}",
                            face.lod
                        );
                    }
                    // A wink: the open eye (viewer's left) only.
                    let wink = mon(Expression::Playful, 0, 0, 0.32);
                    assert_eq!(wink.face_text(), Some("(o_<)"));
                    let face = render_face(&wink, renderer, &colors, case, colors.ink(10));
                    for pos in plates(&face, sclera) {
                        let (uv, _) = head_uv(&wink, renderer, face.projection, pos);
                        assert!(
                            uv.is_some_and(|uv| uv[0] < 0.5),
                            "(o_<) {depth:?} {renderer:?} {case:?} {:?}: plate on the closed wink eye at {pos:?}",
                            face.lod
                        );
                    }
                    // Rear and profile heads, flat and pitched like the Room.
                    for (yaw, pitch) in [
                        (90, 0.32),
                        (180, 0.32),
                        (270, 0.32),
                        (180, 0.52),
                        (180, 0.9),
                        (150, 0.6),
                        (210, 0.6),
                    ] {
                        let rear = mon(Expression::Surprised, 0, yaw, pitch);
                        let face = render_face(&rear, renderer, &colors, case, colors.ink(10));
                        let cells = plates(&face, sclera);
                        assert!(
                            cells.is_empty(),
                            "(O_O) yaw {yaw} pitch {pitch} {depth:?} {renderer:?} {case:?}: plate on a rear or profile face at {cells:?}"
                        );
                    }
                    // Open eyes, frontal and oblique: plates only on wholly
                    // authorized head cells, never on the mouth.
                    for yaw in [0, 20, 35, 325, 340] {
                        let open = mon(Expression::Surprised, 0, yaw, 0.32);
                        let face = render_face(&open, renderer, &colors, case, colors.ink(10));
                        let texture = FaceTexture::new(&open).unwrap();
                        for pos in plates(&face, sclera) {
                            let label = format!(
                                "(O_O) yaw {yaw} {depth:?} {renderer:?} {case:?} {:?} at {pos:?}",
                                face.lod
                            );
                            let (_, complete) = head_uv(&open, renderer, face.projection, pos);
                            if face.lod == FaceLod::Native || face.lod == FaceLod::Shaded {
                                assert!(
                                    complete,
                                    "{label}: plate on a nose, occluder or off-head sample"
                                );
                            }
                            let (dx, dy) = renderer.packing();
                            let mut any = false;
                            for y in 0..dy {
                                for x in 0..dx {
                                    let px = u32::from(pos.0) * u32::from(dx) + u32::from(x);
                                    let py = u32::from(pos.1) * u32::from(dy) + u32::from(y);
                                    any |=
                                        projected_head_uv(&open, face.projection, px, py).is_some();
                                }
                            }
                            assert!(any, "{label}: plate on a cell with no visible head");
                            if face.lod != FaceLod::Native {
                                let bounds = FaceTexture::bounds(&open).unwrap();
                                let area = face.after.area;
                                let (l, t) =
                                    face.projection.cell(bounds.left, bounds.top, renderer);
                                let (r, b) =
                                    face.projection.cell(bounds.right, bounds.bottom, renderer);
                                let fit = FaceFit::of(
                                    &open,
                                    renderer,
                                    face.projection,
                                    area,
                                    texture,
                                    [l, t, r, b],
                                );
                                // Some face-plane sample of the cell lies in an
                                // eye's glyph box: a cell wholly on the mouth or
                                // a parenthesis is never whitened.
                                let mut glyphs = Vec::new();
                                for y in 0..dy {
                                    for x in 0..dx {
                                        let px = u32::from(pos.0) * u32::from(dx) + u32::from(x);
                                        let py = u32::from(pos.1) * u32::from(dy) + u32::from(y);
                                        if let Some((uv, _)) =
                                            projected_face_uv(&open, face.projection, px, py)
                                        {
                                            glyphs.push(texture.glyph_at(fit.map(uv)));
                                        }
                                    }
                                }
                                assert!(!glyphs.is_empty(), "{label}: plate off the face plane");
                                assert!(
                                    glyphs.iter().any(|&g| eye_slot(texture.text, g)),
                                    "{label}: plate on glyphs {glyphs:?} (the mouth or a parenthesis)"
                                );
                            }
                        }
                    }
                }
            }
        }
        assert!(checked > 100, "only {checked} closed-eye faces were drawn");
    }

    /// FACE-1: quadrant/braille atlas strokes are face ink or pupil ink over
    /// the head, never holes through to the room, so the same face reads the
    /// same over any room colour; plates grow with the face.
    #[test]
    fn atlas_strokes_use_face_ink_and_plates_grow_with_the_face() {
        let config = Config::sample().unwrap();
        let colors = Colors::from_config(&config, Depth::Rgb).unwrap();
        let sclera = Sclera::of(&colors);
        let open = mon(Expression::Idle, 0, 0, 0.32);
        let stroke = face_ink(&open, &colors, InkRegion::TextBox);
        let mut atlas = 0;
        for renderer in [Renderer::Quadrant, Renderer::Braille] {
            for zoom in [100, 200, 300, 400] {
                let case = ((71, 20), CameraView::Face, zoom);
                let teal = render_face(&open, renderer, &colors, case, colors.ink(13));
                let mint = render_face(&open, renderer, &colors, case, colors.ink(15));
                if !teal.drawn {
                    continue;
                }
                atlas += usize::from(teal.lod == FaceLod::Atlas);
                // Every cell wholly on the head (strokes, plates and the
                // quiet face zone between them) is the Mon's own colours.
                let area = teal.after.area;
                for row in area.y..area.bottom() {
                    for col in area.x..area.right() {
                        let pos = (col, row);
                        let (_, complete) = head_uv(&open, renderer, teal.projection, pos);
                        if !complete {
                            continue;
                        }
                        let (a, b) = (&teal.after[pos], &mint.after[pos]);
                        assert_eq!(
                            (a.symbol(), a.fg, a.bg),
                            (b.symbol(), b.fg, b.bg),
                            "{renderer:?} @{zoom}: face cell {pos:?} changes with the room"
                        );
                        assert!(
                            a.bg != colors.ink(13),
                            "{renderer:?} @{zoom}: room shows through the face at {pos:?}"
                        );
                        // Atlas strokes are drawn in face ink (pupil ink and
                        // eye white on a plate), never carved from the body.
                        if teal.lod == FaceLod::Atlas && teal.before[pos] != *a {
                            let inks = if a.fg == sclera.white || a.bg == sclera.white {
                                [sclera.white, sclera.ink]
                            } else {
                                [stroke, stroke]
                            };
                            assert!(
                                inks.contains(&a.fg) || inks.contains(&a.bg),
                                "{renderer:?} @{zoom}: atlas stroke without face ink at {pos:?}: {a:?}"
                            );
                        }
                    }
                }
            }
        }
        assert!(atlas >= 2, "the quadrant/braille atlas was not exercised");
        // Plate area (packed pixels) never shrinks as the Face zoom grows.
        for renderer in RENDERERS {
            let mut previous = 0;
            let mut previous_lod = None;
            for zoom in (100..=400).step_by(25) {
                let face = render_face(
                    &open,
                    renderer,
                    &colors,
                    ((71, 20), CameraView::Face, zoom),
                    colors.ink(10),
                );
                let (dx, dy) = renderer.packing();
                let area = plates(&face, sclera).len() * usize::from(dx * dy);
                if previous_lod == Some(face.lod) && face.drawn {
                    assert!(
                        area * 10 >= previous * 9,
                        "{renderer:?} @{zoom} {:?}: plate area fell from {previous} to {area}",
                        face.lod
                    );
                }
                previous = area;
                previous_lod = face.drawn.then_some(face.lod);
            }
        }
    }
}

#[cfg(test)]
mod face_transition_tests {
    //! F3 transitions. Once a live camera remembers its face detail, the face
    //! tiers keep a Schmitt hysteresis and blend glyph by glyph, one tier per
    //! configured transition, so zoom, rotation and resize never swap a whole
    //! face between native characters, structural strokes and the atlas in
    //! one frame. The overlay's minified contour tier fades in voxel by voxel.
    use super::*;
    use crate::{
        game::Stage,
        sprite::{Appearance, Expression, SpriteStyle},
    };
    use std::cell::Cell;

    /// Root's live fox (seed 513).
    const FOX: Appearance = Appearance {
        shape: 1,
        body: 8,
        accent: 3,
    };
    /// The vendor `zoom_transition_frames`.
    const FRAMES: usize = 8;

    fn mon(yaw: u16) -> Sprite {
        Sprite::voxel_with_appearance(
            Stage::Baby,
            Expression::Idle,
            SpriteStyle::SixteenBit,
            0,
            yaw,
            false,
            FOX,
        )
    }

    fn camera(view: CameraView, zoom_percent: u16) -> Camera {
        Camera {
            view,
            from_view: view,
            zoom_percent,
            transition_percent: 100,
            ..Camera::default()
        }
    }

    fn clock(frame: usize) -> DetailClock {
        DetailClock {
            frame,
            frames: FRAMES,
        }
    }

    fn projection(
        sprite: &Sprite,
        renderer: Renderer,
        (width, height): (u16, u16),
        (view, zoom): (CameraView, u16),
    ) -> Projection {
        Projection::camera(
            sprite,
            SolidBounds::of(sprite).unwrap(),
            Rect::new(0, 0, width, height),
            renderer,
            0,
            (500, 700),
            camera(view, zoom),
        )
    }

    /// The bare Mon over a room-coloured scene, and its projection.
    fn scene(
        sprite: &Sprite,
        renderer: Renderer,
        colors: &Colors,
        size: (u16, u16),
        framing: (CameraView, u16),
    ) -> (Projection, Buffer) {
        let area = Rect::new(0, 0, size.0, size.1);
        let mut buf = Buffer::empty(area);
        for cell in &mut buf.content {
            cell.set_bg(colors.ink(10));
        }
        let projection = projection(sprite, renderer, size, framing);
        draw_projection(sprite, renderer, colors, projection, area, &mut buf);
        (projection, buf)
    }

    /// The fitted text box's `[cells per glyph, rows]` and atlas availability.
    fn layout(
        sprite: &Sprite,
        renderer: Renderer,
        colors: &Colors,
        size: (u16, u16),
        framing: (CameraView, u16),
    ) -> FaceLayout {
        let texture = FaceTexture::new(sprite).unwrap();
        let area = Rect::new(0, 0, size.0, size.1);
        let projection = projection(sprite, renderer, size, framing);
        FaceLayout::of(sprite, renderer, colors, projection, area, texture).unwrap()
    }

    /// The tier each glyph of a `len`-glyph face is drawn with at `level`.
    fn glyph_tiers(level: f32, len: usize) -> Vec<FaceLod> {
        let (low, high, upper) = face_blend(level, len);
        (0..len)
            .map(|i| if draws_glyph(upper, i) { high } else { low })
            .collect()
    }

    /// Consecutive frames change the tier of at most one symmetric ring of
    /// glyphs (the middle glyph, or one mirrored pair), and never by two tiers.
    fn assert_one_ring_per_frame(tiers: &[Vec<FaceLod>], label: &str) -> usize {
        let mut worst = 0;
        for (k, pair) in tiers.windows(2).enumerate() {
            let len = pair[0].len();
            let changed: Vec<usize> = (0..len).filter(|&i| pair[0][i] != pair[1][i]).collect();
            worst = worst.max(changed.len());
            assert!(
                changed.len() <= 2 && changed.iter().all(|&i| changed.contains(&(len - 1 - i))),
                "{label}: frame {}: glyphs {changed:?} changed tier in one frame ({:?} -> {:?})",
                k + 1,
                pair[0],
                pair[1]
            );
            for &i in &changed {
                assert!(
                    (pair[0][i].level() - pair[1][i].level()).abs() <= 1.0,
                    "{label}: frame {}: glyph {i} jumped two tiers",
                    k + 1
                );
            }
        }
        worst
    }

    /// The UI's zoom easing: each press retargets from the zoom drawn now.
    struct Zoom {
        origin: u16,
        target: u16,
        started: usize,
    }

    impl Zoom {
        fn at(&self, frame: usize) -> u16 {
            let t = (frame.saturating_sub(self.started) * 100 / FRAMES).min(100) as i32;
            let eased = t * t * (300 - 2 * t) / 10000;
            (i32::from(self.origin)
                + (i32::from(self.target) - i32::from(self.origin)) * eased / 100)
                .clamp(50, 400) as u16
        }

        fn press(&mut self, frame: usize, step: i32) {
            self.origin = self.at(frame);
            self.target = (i32::from(self.target) + step).clamp(50, 400) as u16;
            self.started = frame;
        }
    }

    /// One drawn frame of a live camera.
    struct Frame {
        level: f32,
        memo: DetailMemo,
    }

    /// A zoom ramp as the UI eases it: `presses` zoom actions of `step`, one
    /// per frame from frame 1, then the transition settles.
    fn ramp(
        sprite: &Sprite,
        renderer: Renderer,
        colors: &Colors,
        (view, size): (CameraView, (u16, u16)),
        (start, step, presses): (u16, i32, usize),
    ) -> Vec<Frame> {
        let memo = Cell::new(DetailMemo::default());
        let mut zoom = Zoom {
            origin: start,
            target: start,
            started: 0,
        };
        let mut frames = Vec::new();
        for frame in 0..presses + 3 * FRAMES + 2 {
            if (1..=presses).contains(&frame) {
                zoom.press(frame, step);
            }
            let (projection, before) =
                scene(sprite, renderer, colors, size, (view, zoom.at(frame)));
            let mut buf = before.clone();
            draw_camera_face_with(
                sprite,
                renderer,
                colors,
                projection,
                before.area,
                &mut buf,
                Some((&memo, clock(frame))),
            );
            frames.push(Frame {
                level: memo.get().level(clock(frame)).unwrap(),
                memo: memo.get(),
            });
        }
        frames
    }

    /// T-T1: zoom ramps and jumps in and out, across the native/strokes and
    /// the strokes/atlas thresholds, change one symmetric ring of glyphs per
    /// frame at most, never a whole face, and settle on the Schmitt tier. The
    /// stateless renderer (the behaviour before this change) swaps every
    /// glyph in one frame.
    #[test]
    fn a_zoom_ramp_blends_face_tiers_one_glyph_ring_per_frame() {
        let config = Config::sample().unwrap();
        let colors = Colors::from_config(&config, Depth::Rgb).unwrap();
        let sprite = mon(0);
        let len = FaceTexture::new(&sprite).unwrap().text.len();
        let mut crossings: Vec<(FaceLod, FaceLod)> = Vec::new();
        let (mut blended, mut worst) = (0, 0);
        for (renderer, framing) in [
            (Renderer::HalfBlock, (CameraView::Face, (130, 29))),
            (Renderer::Braille, (CameraView::Face, (130, 29))),
            (Renderer::HalfBlock, (CameraView::Face, (59, 49))),
            (Renderer::Ascii, (CameraView::Pet, (40, 16))),
            (Renderer::HalfBlock, (CameraView::Pet, (40, 16))),
            (Renderer::Quadrant, (CameraView::Pet, (59, 49))),
        ] {
            for ramp_case in [(50, 25, 14), (400, -350, 1)] {
                let label = format!("{renderer:?} {framing:?} zoom {ramp_case:?}");
                let frames = ramp(&sprite, renderer, &colors, framing, ramp_case);
                let atlas =
                    layout(&sprite, renderer, &colors, framing.1, (framing.0, 100)).pixel_atlas;
                let stateless: Vec<FaceLod> = frames
                    .iter()
                    .map(|f| face_tier(f.memo.metric.map(f64::from), atlas, None))
                    .collect();
                crossings.extend(
                    stateless
                        .windows(2)
                        .filter(|p| p[0] != p[1])
                        .map(|p| (p[0], p[1])),
                );
                let tiers: Vec<Vec<FaceLod>> =
                    frames.iter().map(|f| glyph_tiers(f.level, len)).collect();
                worst = worst.max(assert_one_ring_per_frame(&tiers, &label));
                blended += tiers
                    .iter()
                    .filter(|t| t.iter().any(|tier| *tier != t[0]))
                    .count();
                let last = frames.last().unwrap();
                assert!(
                    tiers
                        .last()
                        .unwrap()
                        .iter()
                        .all(|t| Some(*t) == last.memo.tier),
                    "{label}: the face did not settle on its tier {:?}",
                    last.memo.tier
                );
            }
        }
        let count = |a: FaceLod, b: FaceLod| {
            crossings
                .iter()
                .filter(|c| **c == (a, b) || **c == (b, a))
                .count()
        };
        let (native, atlas) = (
            count(FaceLod::Native, FaceLod::Shaded),
            count(FaceLod::Shaded, FaceLod::Atlas),
        );
        println!(
            "A8 zoom ramps: stateless crossings native/strokes {native}, strokes/atlas {atlas} \
             (each swaps {len}/{len} glyphs in one frame); remembered: at most {worst}/{len} \
             glyphs per frame, {blended} mixed frames"
        );
        assert!(
            native >= 4 && atlas >= 4,
            "the ramps must cross both thresholds ({crossings:?})"
        );
        assert!(
            blended >= native + atlas,
            "too few mixed frames ({blended})"
        );
    }

    /// T-T2: at either tier threshold the remembered tier holds while the
    /// stateless tier flips on every jitter; the remembered tier enters the
    /// strokes only from 3.5 cells per glyph and leaves them only below 3,
    /// enters the atlas from 8 and leaves it only below 7; a turntable
    /// crosses each threshold at most twice per revolution.
    #[test]
    fn face_tiers_hold_at_a_boundary_with_hysteresis() {
        let config = Config::sample().unwrap();
        let colors = Colors::from_config(&config, Depth::Rgb).unwrap();
        let sprite = mon(0);
        let renderer = Renderer::HalfBlock;
        let framings = [
            (CameraView::Face, (130u16, 29u16)),
            (CameraView::Pet, (130, 29)),
            (CameraView::Pet, (59, 49)),
            (CameraView::Pet, (40, 16)),
        ];
        let metric = |(view, size): (CameraView, (u16, u16)), zoom: u16| {
            let face = layout(&sprite, renderer, &colors, size, (view, zoom));
            assert!(face.pixel_atlas, "the fox's half-block face has an atlas");
            face.metric
        };
        let tier = |framing, zoom| face_tier(metric(framing, zoom), true, None);
        let zooms: Vec<u16> = (50..=400).step_by(2).collect();
        let below = |m: [f64; 2], floor: [f64; 2]| m[0] < floor[0] || m[1] < floor[1];
        // Jitter between the two neighbouring zooms that straddle each exact
        // threshold, starting on the tier below (native) or above (atlas).
        let mut edges = Vec::new();
        for (from, to) in [
            (FaceLod::Native, FaceLod::Shaded),
            (FaceLod::Atlas, FaceLod::Shaded),
        ] {
            let (framing, jitter) = framings
                .iter()
                .find_map(|&framing| {
                    zooms
                        .windows(2)
                        .find(|p| {
                            let (a, b) = (tier(framing, p[0]), tier(framing, p[1]));
                            a != b && (a == from || b == from) && (a == to || b == to)
                        })
                        .map(|p| (framing, [p[0], p[1]]))
                })
                .expect("a framing whose zoom range crosses the threshold");
            let tiers = jitter.map(|z| tier(framing, z));
            edges.push((framing, jitter[1]));
            let start = jitter[usize::from(tiers[1] == from)];
            let m = metric(framing, start);
            let mut memo = DetailMemo::default()
                .step(clock(0), m, |p| face_tier(m, true, p))
                .0;
            assert_eq!(memo.tier, Some(from));
            let (mut held, mut stateless) = (0, 0);
            for k in 1..=20 {
                let m = metric(framing, jitter[k % 2]);
                let before = memo.tier;
                memo = memo.step(clock(k), m, |p| face_tier(m, true, p)).0;
                held += usize::from(memo.tier != before);
                stateless +=
                    usize::from(tier(framing, jitter[k % 2]) != tier(framing, jitter[(k + 1) % 2]));
            }
            println!(
                "hysteresis {from:?}/{to:?} {framing:?} between {jitter:?}% ({:?} / {:?}): \
                 stateless flips {stateless}/20, remembered {held}/20",
                metric(framing, jitter[0]),
                metric(framing, jitter[1])
            );
            assert_eq!(stateless, 20);
            assert_eq!(
                held, 0,
                "{from:?} flipped at the boundary {jitter:?} {held} times"
            );
        }
        // Sweeps in and out: the remembered tier switches only past each
        // threshold's far side.
        let mut switches = Vec::new();
        let sweep: Vec<u16> = zooms
            .iter()
            .copied()
            .chain(zooms.iter().rev().copied())
            .collect();
        for framing in framings {
            let mut memo = DetailMemo::default();
            for (k, zoom) in sweep.iter().enumerate() {
                let m = metric(framing, *zoom);
                let before = memo.tier;
                memo = memo
                    .step(clock(k * 3 * FRAMES), m, |p| face_tier(m, true, p))
                    .0;
                if let (Some(from), Some(to)) = (before, memo.tier) {
                    if from != to {
                        switches.push((from, to, m, framing, *zoom));
                    }
                }
            }
        }
        let has = |from, to| switches.iter().any(|s| s.0 == from && s.1 == to);
        assert!(
            has(FaceLod::Native, FaceLod::Shaded)
                && has(FaceLod::Shaded, FaceLod::Atlas)
                && has(FaceLod::Atlas, FaceLod::Shaded)
                && has(FaceLod::Shaded, FaceLod::Native),
            "the sweeps must cross both thresholds both ways: {switches:?}"
        );
        for (from, to, m, framing, zoom) in &switches {
            let ok = match (from, to) {
                (FaceLod::Native, FaceLod::Shaded) => !below(*m, [3.5, 3.5]),
                (FaceLod::Shaded, FaceLod::Native) => below(*m, [3.0, 3.0]),
                (FaceLod::Shaded, FaceLod::Atlas) => !below(*m, [8.0, 8.0]),
                (FaceLod::Atlas, FaceLod::Shaded) => below(*m, [7.0, 7.0]),
                _ => true,
            };
            assert!(ok, "{from:?} -> {to:?} at {m:?} ({framing:?} {zoom}%)");
        }
        // Turntable: one revolution in 5 degree steps at each threshold's zoom.
        let turntable: Vec<Sprite> = (0..=360u16).step_by(5).map(|yaw| mon(yaw % 360)).collect();
        for ((view, size), zoom) in edges {
            let (mut remembered, mut stateless) = (0, 0);
            let mut memo = DetailMemo::default();
            let mut previous = None;
            for (k, sprite) in turntable.iter().enumerate() {
                let Some(texture) = FaceTexture::new(sprite) else {
                    continue;
                };
                let area = Rect::new(0, 0, size.0, size.1);
                let projection = projection(sprite, renderer, size, (view, zoom));
                let Some(face) =
                    FaceLayout::of(sprite, renderer, &colors, projection, area, texture)
                else {
                    continue;
                };
                let m = face.metric;
                let before = memo.tier;
                memo = memo.step(clock(k), m, |p| face_tier(m, true, p)).0;
                remembered += usize::from(before.is_some() && before != memo.tier);
                let now = face_tier(m, true, None);
                stateless += usize::from(previous.is_some_and(|p| p != now));
                previous = Some(now);
            }
            println!(
                "turntable {view:?} {size:?} {zoom}%: stateless tier changes {stateless}, \
                 remembered {remembered}"
            );
            assert!(
                remembered <= 4,
                "turntable at {zoom}%: {remembered} tier changes in one revolution"
            );
        }
    }

    /// T-T3: a resize sequence through RoomWidget (the UI's wiring): the
    /// first frame after a resize keeps the drawn tiers, each frame changes
    /// one glyph ring at most (an interrupted resize included), and every
    /// hold settles on its Schmitt tier within one transition per tier.
    #[test]
    fn a_resize_sequence_blends_face_tiers_from_the_drawn_state() {
        let config = Config::sample().unwrap();
        let colors = Colors::from_config(&config, Depth::Rgb).unwrap();
        let sprite = mon(0);
        let len = FaceTexture::new(&sprite).unwrap().text.len();
        let hold = 2 * FRAMES + 2;
        let steps: Vec<((u16, u16), usize)> = vec![
            ((130, 29), hold),
            ((110, 26), hold),
            ((90, 22), hold),
            ((70, 18), hold),
            ((50, 16), hold),
            ((36, 12), hold),
            ((24, 10), hold),
            ((130, 29), hold),
            // Interrupted: shrink, then grow again three frames in.
            ((24, 10), 3),
            ((110, 26), hold),
        ];
        let mut changes = Vec::new();
        for (renderer, framing) in [
            (Renderer::HalfBlock, (CameraView::Face, 100)),
            (Renderer::Braille, (CameraView::Face, 100)),
            (Renderer::HalfBlock, (CameraView::Pet, 200)),
            (Renderer::HalfBlock, (CameraView::Pet, 100)),
            (Renderer::Ascii, (CameraView::Pet, 100)),
        ] {
            let mut settled = Vec::new();
            let label = format!("{renderer:?} {framing:?}");
            let memo = Cell::new(DetailMemo::default());
            let mut frame = 0;
            let mut tiers: Vec<Vec<FaceLod>> = Vec::new();
            for &((width, height), frames) in &steps {
                for k in 0..frames {
                    let area = Rect::new(0, 0, width, height);
                    let mut buf = Buffer::empty(area);
                    // What this frame would draw had the size not changed.
                    let unchanged = memo
                        .get()
                        .level(clock(frame))
                        .map(|level| glyph_tiers(level, len));
                    RoomWidget {
                        world: None,
                        sprite: &sprite,
                        detail: SpriteStyle::SixteenBit,
                        renderer,
                        scale: 0,
                        colors: &colors,
                        expression: Expression::Idle,
                        frame,
                        position: (500, 700),
                        camera: camera(framing.0, framing.1),
                        habitat: None,
                        face_detail: Some((&memo, clock(frame))),
                    }
                    .render(area, &mut buf);
                    let level = memo.get().level(clock(frame)).unwrap();
                    let now = glyph_tiers(level, len);
                    if k == 0 {
                        if let Some(unchanged) = unchanged {
                            assert_eq!(
                                now, unchanged,
                                "{label}: the first frame after the resize to {width}x{height} \
                                 changed the face tiers"
                            );
                        }
                    }
                    tiers.push(now);
                    frame += 1;
                }
                if frames == hold {
                    let memo = memo.get();
                    assert!(
                        tiers.last().unwrap().iter().all(|t| Some(*t) == memo.tier),
                        "{label}: {width}x{height} did not settle on {:?}",
                        memo.tier
                    );
                    settled.push(memo.tier.unwrap());
                }
            }
            assert_one_ring_per_frame(&tiers, &label);
            println!("resize sequence {label}: settled {settled:?}");
            changes.extend(
                settled
                    .windows(2)
                    .filter(|p| p[0] != p[1])
                    .map(|p| (p[0].min(p[1]), p[0].max(p[1]))),
            );
        }
        assert!(
            changes.contains(&(FaceLod::Native, FaceLod::Shaded))
                && changes.contains(&(FaceLod::Shaded, FaceLod::Atlas)),
            "the sizes must change the face tier across both thresholds ({changes:?})"
        );
    }

    /// T-T4: the memo's step is pure and bounded: a subject's first frame
    /// takes its tier at once; a new tier eases from the drawn level, one
    /// tier per transition, monotonically; an interrupted blend continues
    /// from what was drawn; the Schmitt table and the glyph rings.
    #[test]
    fn detail_memo_steps_are_pure_bounded_and_interruptible() {
        let to = |tier: FaceLod| move |_: Option<FaceLod>| tier;
        let (memo, level) = DetailMemo::default().step(clock(5), [5.0, 5.0], to(FaceLod::Shaded));
        assert_eq!(level, 1.0);
        assert_eq!(memo.step(clock(5), [5.0, 5.0], to(FaceLod::Shaded)).0, memo);
        let (atlas, level) = memo.step(clock(10), [9.0, 9.0], to(FaceLod::Atlas));
        assert_eq!(level, 1.0, "a new tier starts from the drawn level");
        let mut previous = level;
        for frame in 11..=10 + FRAMES {
            let level = atlas.level(clock(frame)).unwrap();
            assert!(level >= previous && level - previous <= 1.5 / FRAMES as f32 + 1e-4);
            assert_eq!(level == 2.0, frame == 10 + FRAMES, "frame {frame}: {level}");
            previous = level;
        }
        let drawn = atlas.level(clock(13)).unwrap();
        let (back, level) = atlas.step(clock(13), [2.0, 2.0], to(FaceLod::Native));
        assert_eq!(level, drawn, "an interrupted blend jumped");
        let total = (FRAMES as f32 * drawn).ceil() as usize;
        assert!(back.level(clock(13 + total - 1)).unwrap() > 0.0);
        assert_eq!(back.level(clock(13 + total)).unwrap(), 0.0);
        // Schmitt table.
        for (metric, previous, tier) in [
            ([3.2, 9.0], None, FaceLod::Shaded),
            ([3.2, 9.0], Some(FaceLod::Native), FaceLod::Native),
            ([3.6, 9.0], Some(FaceLod::Native), FaceLod::Shaded),
            ([3.2, 9.0], Some(FaceLod::Shaded), FaceLod::Shaded),
            ([2.9, 9.0], Some(FaceLod::Shaded), FaceLod::Native),
            ([7.5, 9.0], None, FaceLod::Shaded),
            ([7.5, 9.0], Some(FaceLod::Atlas), FaceLod::Atlas),
            ([6.9, 9.0], Some(FaceLod::Atlas), FaceLod::Shaded),
            ([8.0, 8.0], Some(FaceLod::Shaded), FaceLod::Atlas),
            ([2.9, 9.0], Some(FaceLod::Atlas), FaceLod::Native),
            ([9.0, 3.4], Some(FaceLod::Native), FaceLod::Native),
        ] {
            assert_eq!(
                face_tier(metric, true, previous),
                tier,
                "{metric:?} after {previous:?}"
            );
        }
        assert_eq!(face_tier([9.0, 9.0], false, None), FaceLod::Shaded);
        // Rings: middle first, then mirrored pairs outwards.
        assert_eq!(face_blend(0.2, 5).2, 0b00100);
        assert_eq!(face_blend(0.6, 5).2, 0b01110);
        assert_eq!(face_blend(0.9, 5).2, 0b11111);
        assert_eq!(face_blend(1.4, 6).2, 0b001100);
        assert_eq!(face_blend(1.0, 5), (FaceLod::Shaded, FaceLod::Atlas, 0));
        assert_eq!(face_blend(2.0, 7).2, 0b111_1111);
    }

    /// T-T5: a blended face draws every glyph whole from one tier. Between
    /// strokes and the atlas each cell equals the lower or the upper tier's
    /// own render and both tiers' ink appears. Between native characters and
    /// strokes the face keeps one layout: the upper glyphs are exactly the
    /// strokes tier's, and every lower glyph is its own character inside its
    /// own glyph box on the face plane (never a separate native run).
    #[test]
    fn blended_faces_draw_every_glyph_from_one_tier_in_one_layout() {
        let config = Config::sample().unwrap();
        let colors = Colors::from_config(&config, Depth::Rgb).unwrap();
        let sprite = mon(0);
        let texture = FaceTexture::new(&sprite).unwrap();
        let len = texture.text.len();
        let mut checked = Vec::new();
        for renderer in [
            Renderer::Ascii,
            Renderer::HalfBlock,
            Renderer::Quadrant,
            Renderer::Braille,
        ] {
            for (size, framing) in [
                ((130, 29), (CameraView::Face, 100)),
                ((130, 29), (CameraView::Face, 160)),
                ((59, 49), (CameraView::Face, 200)),
                ((40, 16), (CameraView::Pet, 100)),
                ((59, 49), (CameraView::Pet, 100)),
            ] {
                let label = format!("{renderer:?} {size:?} {framing:?}");
                let face = layout(&sprite, renderer, &colors, size, framing);
                let tier = face_tier(face.metric, face.pixel_atlas, None);
                // Five eighths of the way from the tier below: the mouth and
                // the eyes are on this tier, the parentheses still below.
                let from = match tier {
                    FaceLod::Native => continue,
                    FaceLod::Shaded => FaceLod::Native,
                    FaceLod::Atlas => FaceLod::Shaded,
                };
                let (projection, before) = scene(&sprite, renderer, &colors, size, framing);
                let area = before.area;
                let memo = Cell::new(DetailMemo {
                    tier: Some(tier),
                    from: from.level(),
                    started: 0,
                    metric: [0.0; 2],
                });
                let mut mixed = before.clone();
                draw_camera_face_with(
                    &sprite,
                    renderer,
                    &colors,
                    projection,
                    area,
                    &mut mixed,
                    Some((&memo, clock(FRAMES * 5 / 8))),
                );
                let level = memo.get().level(clock(FRAMES * 5 / 8)).unwrap();
                assert!(level > from.level() && level < tier.level());
                let upper = face_blend(level, len).2;
                if from == FaceLod::Native {
                    let mut strokes = before.clone();
                    assert!(draw_shaded_face(
                        &sprite,
                        renderer,
                        &colors,
                        projection,
                        area,
                        &mut strokes,
                        (texture, face.rect, face.stroke_color, face.fit, upper, 0),
                    ));
                    for (i, cell) in strokes.content.iter().enumerate() {
                        if *cell != before.content[i] {
                            assert_eq!(
                                &mixed.content[i], cell,
                                "{label}: cell {i} of an upper glyph is not its stroke"
                            );
                        }
                    }
                    let (dx, dy) = renderer.packing();
                    for glyph in (0..len).filter(|&g| !draws_glyph(upper, g)) {
                        let character = char::from(texture.text.as_bytes()[glyph]);
                        let placed = (0..area.height).any(|row| {
                            (0..area.width).any(|col| {
                                let (m, s) = (&mixed[(col, row)], &strokes[(col, row)]);
                                let px = u32::from(col) * u32::from(dx) + u32::from(dx / 2);
                                let py = u32::from(row) * u32::from(dy) + u32::from(dy / 2);
                                m != s
                                    && m.symbol().starts_with(character)
                                    && projected_face_uv(&sprite, projection, px, py).is_some_and(
                                        |(uv, _)| texture.glyph_at(face.fit.map(uv)) == glyph,
                                    )
                            })
                        });
                        assert!(
                            placed,
                            "{label}: lower glyph {glyph} {character:?} is not its own character \
                             in its glyph box"
                        );
                    }
                    checked.push((from, tier));
                    continue;
                }
                let pure = |level: f32| {
                    let memo = Cell::new(DetailMemo {
                        tier: Some(FaceLod::at(level as usize)),
                        from: level,
                        started: 0,
                        metric: [0.0; 2],
                    });
                    let mut buf = before.clone();
                    draw_camera_face_with(
                        &sprite,
                        renderer,
                        &colors,
                        projection,
                        area,
                        &mut buf,
                        Some((&memo, clock(0))),
                    );
                    buf
                };
                let low = pure(from.level());
                let high = pure(tier.level());
                // Each tier's own ink, where the two tiers differ.
                let (mut from_low, mut from_high) = (0, 0);
                for (i, cell) in mixed.content.iter().enumerate() {
                    let (l, h, b) = (&low.content[i], &high.content[i], &before.content[i]);
                    if l == h {
                        assert_eq!(cell, l, "{label}: cell {i} changed");
                        continue;
                    }
                    assert!(
                        cell == l || cell == h,
                        "{label}: cell {i} is from neither tier"
                    );
                    from_low += usize::from(cell == l && l != b);
                    from_high += usize::from(cell == h && h != b);
                }
                assert!(
                    from_low > 0 && from_high > 0,
                    "{label}: a {from:?}/{tier:?} blend drew one tier ({from_low} lower, \
                     {from_high} upper ink cells)"
                );
                checked.push((from, tier));
            }
        }
        println!("blends judged: {checked:?}");
        assert!(
            checked.contains(&(FaceLod::Native, FaceLod::Shaded))
                && checked.contains(&(FaceLod::Shaded, FaceLod::Atlas)),
            "both blend kinds must be judged ({checked:?})"
        );
    }

    /// T-T6: across the minified band each voxel gains its shallow contours
    /// at its own share, so a zoom never drops or adds every shallow contour
    /// at once; outside the band the old rule holds exactly.
    #[test]
    fn minified_contours_fade_in_voxel_by_voxel() {
        let config = Config::sample().unwrap();
        let colors = Colors::from_config(&config, Depth::Rgb).unwrap();
        const LINES: &str = "-_|/\\^v<>";
        let share = |cells_per_voxel: f32| {
            let drawn = (0..400)
                .filter(|&k| {
                    let overlay = OverlaySample {
                        voxel: [k % 20, k / 20, 3],
                        facet: [0, 0, 127],
                        material: 8,
                        depth: 4.0,
                        voxel_span: [cells_per_voxel; 2],
                        outside: 0,
                        contour: DIR_E,
                        deep_contour: 0,
                        crease: 0,
                        seam: 0,
                        neighbours: [([0, 0, 127], 8); 4],
                        face_plane: false,
                    };
                    let surface = std::array::from_fn(|i| {
                        (i == 0).then_some(SurfaceLight {
                            normal: [0.0, 0.0, 1.0],
                            occlusion: 0,
                            edge: false,
                            screen_edge: false,
                            overlay: Some(overlay),
                        })
                    });
                    let pixels = std::array::from_fn(|i| (i == 0).then_some(8));
                    let mut cell = ratatui::buffer::Cell::default();
                    paint_lit_pixels(
                        Renderer::Ascii,
                        &colors,
                        pixels,
                        surface,
                        [false; 8],
                        &mut cell,
                    );
                    cell.symbol()
                        .chars()
                        .next()
                        .is_some_and(|c| LINES.contains(c))
                })
                .count();
            drawn as f64 / 400.0
        };
        let steps: Vec<f32> = (0..=24).map(|i| 0.25 + 0.025 * i as f32).collect();
        let shares: Vec<f64> = steps.iter().map(|c| share(*c)).collect();
        println!(
            "shallow contour share by cells per voxel: {:?}",
            steps.iter().zip(&shares).collect::<Vec<_>>()
        );
        for (c, s) in steps.iter().zip(&shares) {
            if *c <= MINIFIED_CONTOUR_BAND.0 {
                assert_eq!(*s, 0.0, "a minified voxel drew a shallow contour at {c}");
            }
            if *c >= MINIFIED_CONTOUR_BAND.1 {
                assert_eq!(*s, 1.0, "a magnified voxel lost its contour at {c}");
            }
        }
        assert!(
            shares.windows(2).all(|p| p[1] >= p[0]),
            "the fade is not monotone"
        );
        let partial = shares.iter().filter(|s| **s > 0.0 && **s < 1.0).count();
        let step = shares
            .windows(2)
            .map(|p| p[1] - p[0])
            .fold(0.0f64, f64::max);
        assert!(
            partial >= 5 && step <= 0.25,
            "no voxel-by-voxel fade: {partial} partial steps, largest step {step:.3}"
        );
    }
}
