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
    pub view: CameraView,
    pub zoom_percent: u16,
    pub from_view: CameraView,
    pub transition_percent: u16,
}

impl Default for Camera {
    fn default() -> Self {
        Self {
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
        let zoom = f64::from(camera.zoom_percent.clamp(50, 400)) / 100.0;
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
                // Retain the upper silhouette and ears around the actual face,
                // while the lower body extends beyond this closer framing.
                frame.left = cx - bounds.width() * 0.55;
                frame.right = cx + bounds.width() * 0.55;
                frame.top = bounds.top - bounds.height() * 0.025;
                frame.bottom = (cy + bounds.height() * 0.18)
                    .max(bounds.top + bounds.height() * 0.38)
                    .min(bounds.bottom);
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
            let (left, top) = if view == CameraView::Room {
                (
                    (width - bounds.width() * scale) * f64::from(position.0.min(1000)) / 1000.0
                        - bounds.left * scale,
                    (height - bounds.height() * scale_y) * f64::from(position.1.min(1000)) / 1000.0
                        - bounds.top * scale_y,
                )
            } else {
                (
                    width / 2.0 - (frame.left + frame.right) / 2.0 * scale,
                    height / 2.0 - (frame.top + frame.bottom) / 2.0 * scale_y,
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
    Some(SurfaceLight {
        normal,
        occlusion,
        edge,
    })
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
                brightness[i].map_or(base, |value| shade_terminal_color(lighting, base, value))
            }
        })
    });
    let mono = colors.palette.iter().all(|c| *c == Color::Reset);
    match renderer {
        Renderer::Ascii | Renderer::Auto => {
            if let Some(value) = brightness[first].filter(|_| !flat_ink[first]) {
                cell.set_char(lighting.glyph(value));
            }
            cell.set_fg(shaded[first].unwrap());
        }
        Renderer::HalfBlock if mono => {
            if let Some(value) = brightness[first].filter(|_| !flat_ink[first]) {
                cell.set_char(lighting.glyph(value));
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

fn paint_face_character(cell: &mut ratatui::buffer::Cell, colors: &Colors, glyph: char) {
    cell.set_char(glyph).set_fg(colors.ink(0));
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
    for (offset, glyph) in face.text.chars().enumerate() {
        if let Some(cell) = buf.cell_mut(((start + offset as i32) as u16, row as u16)) {
            paint_face_character(cell, colors, glyph);
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
}

impl FaceTexture {
    fn new(sprite: &Sprite) -> Option<Self> {
        let text = sprite.face_text()?;
        (printable_face(text) && text.bytes().all(|b| face_glyph(b).is_some()))
            .then_some(Self { text })
    }

    fn width(self) -> f64 {
        (self.text.len() * 8 - 3) as f64
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
        let grid = [(self.width() + 2.0) * uv[0] - 1.0, 11.0 * uv[1] - 1.0];
        // Coverage, rather than ANY touched ink, keeps side bearings open.
        // Capping the filter prevents minification from growing a solid face.
        // Underresolved heads use independently surface-anchored terminal glyphs.
        let radius = [
            ((self.width() + 2.0) * footprint[0].abs() / 2.0).clamp(0.025, 0.75),
            (11.0 * footprint[1].abs() / 2.0).clamp(0.025, 0.75),
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
        coverage / (4.0 * radius[0] * radius[1]) >= 0.4
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
    projected_surface_uv(sprite, projection, x, y, valid_head_uv)
}

/// The same nearest material sample authorizes every interpolated UV.
/// Derivatives refine ink within that pixel, never its depth/visibility mask.
fn projected_face_uv(
    sprite: &Sprite,
    projection: Projection,
    x: u32,
    y: u32,
) -> Option<([f64; 2], [f64; 2])> {
    projected_surface_uv(sprite, projection, x, y, valid_face_uv)
}

fn projected_surface_uv(
    sprite: &Sprite,
    projection: Projection,
    x: u32,
    y: u32,
    lookup: fn(&Sprite, u32, u32) -> Option<[f64; 2]>,
) -> Option<([f64; 2], [f64; 2])> {
    let (sx, sy) = projection.source_pixel(sprite, x, y)?;
    let uv = lookup(sprite, sx, sy)?;
    let neighbor = |x, y| {
        lookup(sprite, x, y)
            .filter(|next| (next[0] - uv[0]).abs() <= 0.25 && (next[1] - uv[1]).abs() <= 0.25)
    };
    let derivative = |forward: Option<[f64; 2]>, backward: Option<[f64; 2]>| {
        std::array::from_fn::<_, 2, _>(|i| match (forward, backward) {
            (Some(a), Some(b)) => (a[i] - b[i]) / 2.0,
            (Some(a), None) => a[i] - uv[i],
            (None, Some(b)) => uv[i] - b[i],
            _ => 0.0,
        })
    };
    let horizontal = derivative(
        neighbor(sx + 1, sy),
        sx.checked_sub(1).and_then(|x| neighbor(x, sy)),
    );
    let vertical = derivative(
        neighbor(sx, sy + 1),
        sy.checked_sub(1).and_then(|y| neighbor(sx, y)),
    );
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
            let dv = (uv[1] - band) / 0.16;
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
                score += (uv[0] - target_u).powi(2) + (uv[1] - 0.35).powi(2);
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
                paint_face_character(cell, colors, char::from(glyph));
                painted = true;
            }
        }
        return painted;
    }
    let horizontal_tolerance = 3.0 / (texture.width() + 2.0);
    // Start with the upper face. A broad beak can consume a whole terminal
    // cell there, so derive alternate rows from actual legal mouth cells.
    // Every fallback row still belongs to the nearest visible mapped head.
    let mut bands = vec![0.35];
    bands.extend(
        visible
            .iter()
            .filter(|(_, uv, width)| (uv[0] - 0.5).abs() <= horizontal_tolerance.max(width * 0.55))
            .map(|(_, uv, _)| uv[1]),
    );
    bands.sort_by(|a, b| (a - 0.35).abs().total_cmp(&(b - 0.35).abs()));
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
                paint_face_character(cell, colors, char::from(glyph));
                painted = true;
            }
        }
        return painted;
    }
    if let Some(candidates) = compact {
        for (pos, glyph) in candidates.into_iter().flatten() {
            if let Some(cell) = buf.cell_mut(pos) {
                paint_face_character(cell, colors, char::from(glyph));
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
    // Atlas resolution still comes from its original narrow face surface.
    if columns < texture.text.len() as f64 * 5.0 || rows < 4.5 {
        return native_fits && draw_surface_glyphs(sprite, renderer, colors, projection, area, buf);
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
                        cell.set_char(mark).set_fg(colors.ink(0));
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

impl Widget for RoomWidget<'_> {
    fn render(self, area: Rect, buf: &mut Buffer) {
        use crate::sprite::Expression;
        let area = area.intersection(buf.area);
        if area.width == 0 || area.height == 0 {
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
        draw_camera_face(
            self.sprite,
            self.renderer,
            self.colors,
            projection,
            viewport,
            buf,
        );
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
                            assert_eq!(after[(x, y)].fg, colors.ink(0));
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
                        detail: SpriteStyle::SixteenBit,
                        sprite: &sprite,
                        renderer,
                        scale: 0,
                        colors: &colors,
                        expression: Expression::Eating,
                        frame: 1,
                        position: (1000, 0),
                        camera: Camera {
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
                                        assert!(projected_head_uv(&sprite, projection,
                                            u32::from(col) * u32::from(dx) + u32::from(x),
                                            u32::from(row) * u32::from(dy) + u32::from(y)).is_some(),
                                            "{renderer:?} shape{shape} height{height}: tiny text escaped head authority");
                                    }
                                }
                                marks.push((col, cell.symbol().to_owned()));
                            }
                        }
                    }
                    marks.sort_by_key(|p| p.0);
                    let literal: String = marks.iter().map(|p| p.1.as_str()).collect();
                    if !literal.is_empty() {
                        assert!(literal == canonical || literal == core,
                            "{renderer:?} shape{shape} height{height}: incomplete tiny expression {literal}");
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
        let texture = FaceTexture { text: "(^_^)" };
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
        let texture = FaceTexture { text: "(^_^)" };
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
        let texture = FaceTexture { text: "(^_^)" };
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
            let texture = FaceTexture { text };
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
        let texture = FaceTexture { text: "(^_^)" };
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
                    assert!(matches!(literal.as_str(), "^_^" | "(^_^)"),
                        "{renderer:?} {columns} cells: missing eye/mouth or unbalanced parentheses: {literal}");
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
                    assert!(matches!(literal.as_str(), "^_^" | "(^_^)"),
                        "{width}x{height} {renderer:?} {view:?} zoom50 needs native eyes/mouth with balanced optional parentheses: {literal}");
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
                    assert!(literal == canonical || literal == core,
                        "actual bird child Sick {style:?} frame{frame} {renderer:?} head{columns}: missing native eye/mouth or lone bracket: {literal}");
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
                            assert!(draw_camera_face(&sprite, renderer, &colors, projection, area, &mut after),
                                "bird child Sick {style:?} frame{frame} {renderer:?} {view:?} zoom{zoom} width{width}: no complete native core");
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
                            assert!(literal == canonical || literal == core,
                                "bird child Sick {style:?} frame{frame} {renderer:?} {view:?} zoom{zoom} width{width}: {literal}");
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
        let texture = FaceTexture { text: "(^_^)" };
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
                        for y in 0..dy {
                            for x in 0..dx {
                                let px = u32::from(col) * u32::from(dx) + u32::from(x);
                                let py = u32::from(row) * u32::from(dy) + u32::from(y);
                                let sample = projected_face_uv(&sprite, projection, px, py);
                                clear_gap &= sample
                                    .is_some_and(|(uv, footprint)| !texture.sample(uv, footprint));
                                any_backing |= sample.is_some();
                            }
                        }
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
                                        assert!(projected_head_uv(&sprite, projection,
                                            u32::from(col) * u32::from(dx) + u32::from(x),
                                            u32::from(row) * u32::from(dy) + u32::from(y)).is_some(),
                                            "{renderer:?} yaw{yaw}: minified glyph escaped visible head material");
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
                assert_eq!(moved, expected,
                    "{renderer:?} yaw{yaw}: native glyphs must move with the head, never a fixed screen label");
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
