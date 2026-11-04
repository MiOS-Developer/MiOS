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
    config::{Config, parse_rgb},
    sprite::{Sprite, cell_size, fit_scale},
};

#[derive(Clone, Debug)]
pub struct Colors {
    pub palette: Vec<Color>,
    pub background: Color,
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
            let fit = (width / frame.width()).min(height / frame.height());
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
            let (left, top) = if view == CameraView::Room {
                (
                    (width - bounds.width() * scale) * f64::from(position.0.min(1000)) / 1000.0
                        - bounds.left * scale,
                    (height - bounds.height() * scale) * f64::from(position.1.min(1000)) / 1000.0
                        - bounds.top * scale,
                )
            } else {
                (
                    width / 2.0 - (frame.left + frame.right) / 2.0 * scale,
                    height / 2.0 - (frame.top + frame.bottom) / 2.0 * scale,
                )
            };
            Self {
                left: f64::from(area.x) * f64::from(dx) + left,
                top: f64::from(area.y) * f64::from(dy) + top,
                scale,
            }
        };
        let from = mode(camera.from_view);
        let to = mode(camera.view);
        let blend = f64::from(camera.transition_percent.min(100)) / 100.0;
        Self {
            left: from.left + (to.left - from.left) * blend,
            top: from.top + (to.top - from.top) * blend,
            scale: from.scale + (to.scale - from.scale) * blend,
        }
    }

    fn source_pixel(self, sprite: &Sprite, x: u32, y: u32) -> Option<(u32, u32)> {
        let x = ((f64::from(x) + 0.5 - self.left) / self.scale).floor();
        let y = ((f64::from(y) + 0.5 - self.top) / self.scale).floor();
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
            (self.top + y * self.scale) / f64::from(dy),
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
                if upper == lower { '█' } else { '▀' }
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
            for y in 0..dy {
                for x in 0..dx {
                    pixels[usize::from(y * dx + x)] = projection.sample(
                        sprite,
                        u32::from(col) * u32::from(dx) + u32::from(x),
                        u32::from(row) * u32::from(dy) + u32::from(y),
                    );
                }
            }
            if let Some(cell) = buf.cell_mut((col, row)) {
                paint_pixels(renderer, colors, pixels, cell);
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
            cell.set_char(glyph)
                .set_fg(colors.ink(0))
                .set_bg(colors.ink(4));
        }
    }
    true
}

/// A code-native 3x5 stroke font. Only the sprite's authorized expression text
/// selects glyphs; unsupported text retains the literal terminal treatment.
fn face_glyph(glyph: u8) -> Option<[u8; 5]> {
    Some(match glyph {
        b'(' => [1, 2, 4, 2, 1],
        b')' => [4, 2, 1, 2, 4],
        b'^' => [0, 2, 5, 0, 0],
        b'_' => [0, 0, 0, 0, 7],
        b'o' => [0, 2, 5, 5, 2],
        b'O' => [2, 5, 5, 5, 2],
        b'-' => [0, 0, 7, 0, 0],
        b'T' => [7, 2, 2, 2, 2],
        b';' => [0, 2, 0, 2, 4],
        b'>' => [4, 2, 1, 2, 4],
        b'<' => [1, 2, 4, 2, 1],
        b'\'' => [1, 2, 0, 0, 0],
        96 => [4, 2, 0, 0, 0],
        b'=' => [0, 7, 0, 7, 0],
        b'+' => [0, 2, 7, 2, 0],
        b'@' => [2, 5, 7, 4, 3],
        b'u' => [0, 0, 5, 5, 7],
        b'~' => [0, 1, 7, 4, 0],
        b'.' => [0, 0, 0, 2, 0],
        b',' => [0, 0, 0, 2, 4],
        b'*' => [2, 7, 2, 5, 0],
        b'v' => [0, 0, 5, 5, 2],
        b'x' => [0, 5, 2, 5, 0],
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

    fn bit(self, x: i32, y: i32) -> bool {
        if x < 0 || !(0..5).contains(&y) {
            return false;
        }
        let glyph = x as usize / 4;
        let column = x as usize % 4;
        if column == 3 {
            return false;
        }
        self.text
            .as_bytes()
            .get(glyph)
            .and_then(|b| face_glyph(*b))
            .is_some_and(|rows| rows[y as usize] & (1 << (2 - column)) != 0)
    }

    fn sample(self, uv: [f64; 2], footprint: [f64; 2]) -> bool {
        let width = (self.text.len() * 4 - 1) as f64;
        let grid = [(width + 2.0) * uv[0] - 1.0, 7.0 * uv[1] - 1.0];
        let radius = [
            (width + 2.0) * footprint[0].min(1.0) / 2.0,
            7.0 * footprint[1].min(1.0) / 2.0,
        ];
        // The bounded atlas footprint retains thin punctuation in small Pet
        // views. Glyph positions come only from the visible surface's UVs.
        let left = ((grid[0] - radius[0]).floor() as i32).max(0);
        let right = ((grid[0] + radius[0]).floor() as i32).min(width as i32 - 1);
        let top = ((grid[1] - radius[1]).floor() as i32).max(0);
        let bottom = ((grid[1] + radius[1]).floor() as i32).min(4);
        (top..=bottom).any(|y| (left..=right).any(|x| self.bit(x, y)))
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

/// The exact same inverse camera sample authorizes body color and face UV.
/// No interpolation crosses a missing UV, nose, ear, or depth occluder.
fn projected_face_uv(
    sprite: &Sprite,
    projection: Projection,
    x: u32,
    y: u32,
) -> Option<([f64; 2], [f64; 2])> {
    let (sx, sy) = projection.source_pixel(sprite, x, y)?;
    let uv = valid_face_uv(sprite, sx, sy)?;
    let neighbor = |x, y| {
        valid_face_uv(sprite, x, y)
            .filter(|next| (next[0] - uv[0]).abs() <= 0.25 && (next[1] - uv[1]).abs() <= 0.25)
            .unwrap_or(uv)
    };
    let horizontal = neighbor(sx + 1, sy);
    let vertical = neighbor(sx, sy + 1);
    let footprint = std::array::from_fn(|i| {
        ((horizontal[i] - uv[i]).abs() + (vertical[i] - uv[i]).abs()) / projection.scale
    });
    Some((uv, footprint))
}

/// Large expressions are textures on the actual head surface, transformed and
/// occluded by the sprite's depth-tested UV map. Only tiny/legacy views use text.
fn draw_camera_face(
    sprite: &Sprite,
    renderer: Renderer,
    colors: &Colors,
    projection: Projection,
    area: Rect,
    buf: &mut Buffer,
) -> bool {
    let area = area.intersection(buf.area);
    if area.height <= 3 {
        return draw_front_face(sprite, renderer, colors, projection, area, buf);
    }
    let Some(texture) = FaceTexture::new(sprite) else {
        return draw_front_face(sprite, renderer, colors, projection, area, buf);
    };
    let Some(bounds) = FaceTexture::bounds(sprite) else {
        return false;
    };
    // Foreshortening or minification never replaces a mapped surface with a
    // screen-axis label. The atlas footprint and the separate caption handle
    // underresolution; only the actual tiny viewport above uses literal text.
    let (dx, dy) = renderer.packing();
    let (left, top) = projection.cell(bounds.left, bounds.top, renderer);
    let (right, bottom) = projection.cell(bounds.right, bounds.bottom, renderer);
    let mono = colors.palette.iter().all(|c| *c == Color::Reset);
    let mut painted = false;
    for row in (top.floor() as i32).max(i32::from(area.y))
        ..(bottom.ceil() as i32).min(i32::from(area.bottom()))
    {
        for col in (left.floor() as i32).max(i32::from(area.x))
            ..(right.ceil() as i32).min(i32::from(area.right()))
        {
            let mut pixels = [None; 8];
            let mut ink = [false; 8];
            let mut covered = [false; 8];
            for y in 0..dy {
                for x in 0..dx {
                    let i = usize::from(y * dx + x);
                    let px = col as u32 * u32::from(dx) + u32::from(x);
                    let py = row as u32 * u32::from(dy) + u32::from(y);
                    pixels[i] = projection.sample(sprite, px, py);
                    if let Some((uv, footprint)) = projected_face_uv(sprite, projection, px, py) {
                        covered[i] = true;
                        ink[i] = texture.sample(uv, footprint);
                        pixels[i] = Some(if ink[i] { 0 } else { 4 });
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
                    cell.set_char(if ink[0] { '#' } else { ' ' })
                        .set_fg(colors.ink(0))
                        .set_bg(colors.ink(4));
                }
                Renderer::HalfBlock if mono => {
                    if !full_cell {
                        continue;
                    }
                    cell.set_char(if ink[0] || ink[1] { '#' } else { ' ' })
                        .set_fg(Color::Reset)
                        .set_bg(Color::Reset);
                }
                Renderer::HalfBlock => paint_pixels(renderer, colors, pixels, cell),
                Renderer::Quadrant | Renderer::Braille => {
                    // A one-foreground pattern cell cannot replace a nose or
                    // ear subpixel with neutral backing. Keep mixed cells intact.
                    if !full_cell {
                        continue;
                    }
                    let glyph = if renderer == Renderer::Quadrant {
                        quadrant(std::array::from_fn(|i| ink[i]))
                    } else {
                        braille(ink)
                    };
                    cell.set_char(glyph)
                        .set_fg(colors.ink(0))
                        .set_bg(colors.ink(4));
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
        for y in area.y..area.bottom() {
            for x in area.x..area.right() {
                let ground = y >= floor;
                let bg = if ground {
                    let tile = match pattern {
                        1 => (x - area.x) / 5,
                        2 => (x - area.x) / 8 + (y - floor) / 2,
                        3 => 0,
                        4 => (x - area.x) / 4 + (y - floor),
                        5 => (x - area.x) / 7 + (y - floor) / 3,
                        6 => (x - area.x) / 9 + (y - floor),
                        7 => (x - area.x) / 6 + (y - floor) % 2,
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
                    cell.set_char(
                        if !ground && (x - area.x).is_multiple_of(7) && (y - area.y) % 3 == 1 {
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
                        },
                    )
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
            draw_habitat(area, buf, self.colors, view, floor);
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
                assert!(
                    buffer
                        .content
                        .iter()
                        .all(|c| !c.symbol().contains('\u{1b}'))
                );
                assert_eq!(buffer[(0, 0)].symbol(), " ");
                assert_eq!(buffer[(99, 99)].symbol(), " ");
            }
        }
    }
}

fn draw_habitat(area: Rect, buf: &mut Buffer, colors: &Colors, view: HabitatView<'_>, floor: u16) {
    if area.width < 3 || area.height < 2 {
        return;
    }
    let Some(theme) = view.rules.themes.iter().find(|t| t.id == view.state.theme) else {
        return;
    };
    // A seeded structural window belongs to the room, never to paid inventory.
    if area.width >= 12 && area.height >= 6 {
        let (nx, _) = view.state.layout(15);
        let x = area.x + ((u32::from(area.width - 5) * u32::from(nx.min(1000))) / 1000) as u16;
        for (dy, row) in ["+---+", "| . |", "+---+"].iter().enumerate() {
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
        let rows: &[&str] = if area.height < 5 || area.width < 8 {
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
                assert!(
                    text(
                        &buf,
                        Rect::new(area.x, face_y.floor() as u16, area.width, 1)
                    )
                    .contains(front_face.text)
                );
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
            assert!(
                caption.contains(
                    sprite
                        .face_overlay()
                        .map_or(Expression::Eating.kaomoji(1), |f| f.text,)
                )
            );
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
    fn micro_face_uses_two_rows_for_a_real_front_head_without_a_caption() {
        let config = Config::sample().unwrap();
        let colors = Colors::from_config(&config, Depth::Mono).unwrap();
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
                    let face = sprite.face_overlay().expect("front-facing head");
                    let mut buf = Buffer::empty(Rect::new(0, 0, 14, 8));
                    RoomWidget {
                        sprite: &sprite,
                        renderer,
                        scale: 0,
                        colors: &colors,
                        expression: Expression::Idle,
                        frame: 0,
                        position: (500, 500),
                        camera: Camera {
                            view: CameraView::Face,
                            ..Camera::default()
                        },
                        habitat: None,
                    }
                    .render(area, &mut buf);
                    assert!(
                        text(&buf, area).contains(face.text),
                        "{renderer:?} shape {shape} height {height}"
                    );
                    assert!(!text(&buf, area).contains("curious"));
                    assert_eq!(buf[(1, 3)].symbol(), " ");
                    assert_eq!(buf[(12, 3)].symbol(), " ");
                }
            }
        }
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
            top: 40.0 * f64::from(dy) - (f64::from(face.y) + 0.5) * scale,
            scale,
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
                let small = mark_bounds(&small_before, &small_after);
                assert!(
                    small.0 >= 8 && small.1 >= 2,
                    "{renderer:?}: Pet {columns}: {small:?}"
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
                top: 8.0 * f64::from(dy) - f64::from(face.y) * 8.0,
                scale: 8.0,
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
            for frame in 0..4 {
                assert!(
                    expression
                        .kaomoji(frame)
                        .bytes()
                        .all(|b| face_glyph(b).is_some())
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
    fn opaque_face_backing_erases_old_features_in_gaps_without_clearing_the_animal() {
        // This point is above the mouth and farther than one font pixel from
        // any stroke: the old halo implementation left the prior face visible.
        let texture = FaceTexture { text: "(^_^)" };
        assert!(!texture.sample([10.25 / 21.0, 2.25 / 7.0], [0.0, 0.0]));

        let config = Config::sample().unwrap();
        let colors = Colors::from_config(&config, Depth::Indexed).unwrap();
        let sprite = Sprite::voxel(
            Stage::Adult,
            Expression::Happy,
            SpriteStyle::SixteenBit,
            0,
            0,
            false,
        );
        let metadata = sprite.face_overlay().unwrap();
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
                top: 40.0 * f64::from(dy) - (f64::from(metadata.y) + 0.5) * 6.0,
                scale: 6.0,
            };
            let texture = FaceTexture::new(&sprite).unwrap();
            let mut buf = Buffer::empty(area);
            for cell in &mut buf.content {
                cell.set_char('X')
                    .set_fg(colors.ink(5))
                    .set_bg(colors.ink(5));
            }
            let before = buf.clone();
            assert!(draw_camera_face(
                &sprite, renderer, &colors, projection, area, &mut buf
            ));
            let mut cleared_gaps = 0;
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
                        assert_eq!(buf[(col, row)].bg, colors.ink(4), "{renderer:?}");
                        cleared_gaps += 1;
                    }
                    if !any_backing {
                        assert_eq!(buf[(col, row)], before[(col, row)], "{renderer:?}");
                        untouched += 1;
                    }
                }
            }
            assert!(cleared_gaps > 0 && untouched > 0, "{renderer:?}");
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
    fn foreshortened_mapped_faces_never_revert_to_a_screen_axis_literal() {
        let config = Config::sample().unwrap();
        let colors = Colors::from_config(&config, Depth::Indexed).unwrap();
        for yaw in [0, 60] {
            let sprite = Sprite::voxel(
                Stage::Adult,
                Expression::Happy,
                SpriteStyle::SixteenBit,
                0,
                yaw,
                false,
            );
            assert!(sprite.face_text().is_some());
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
                    top: 20.0 * f64::from(dy) - (f64::from(face.y) + 0.5) * scale,
                    scale,
                };
                let area = Rect::new(0, 0, 80, 40);
                let mut buf = Buffer::empty(area);
                draw_projection(&sprite, renderer, &colors, projection, area, &mut buf);
                draw_camera_face(&sprite, renderer, &colors, projection, area, &mut buf);
                let image = text(&buf, area);
                assert!(
                    !image.contains('^') && !image.contains('(') && !image.contains(')'),
                    "{renderer:?} yaw {yaw}: mapped texture became a literal label"
                );
            }
        }
    }

    #[test]
    fn packing_preserves_two_halfblock_colors_and_pattern_masks() {
        let colors = Colors {
            palette: (0..16).map(Color::Indexed).collect(),
            background: Color::Indexed(15),
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
