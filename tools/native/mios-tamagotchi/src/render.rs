// AI-hint: Clipped indexed sprite widget; half-blocks preserve two colors and pattern cells preserve masks.
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
    sprite::{cell_size, fit_scale, Sprite},
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
        for row in 0..rows {
            for col in 0..columns {
                let mut pixels = [None; 8];
                for y in 0..dy {
                    for x in 0..dx {
                        pixels[usize::from(y * dx + x)] = self.sprite.pixel(
                            col * u32::from(dx) + u32::from(x),
                            row * u32::from(dy) + u32::from(y),
                            scale,
                        );
                    }
                }
                let Some(index) = pixels.iter().flatten().next() else {
                    continue;
                };
                let mut fg = self.colors.ink(usize::from(*index));
                let mut bg = buf.cell(((left + col) as u16, (top + row) as u16)).map_or(
                    self.colors.background,
                    |c| {
                        if c.bg == Color::Reset {
                            self.colors.background
                        } else {
                            c.bg
                        }
                    },
                );
                let symbol = match self.renderer {
                    Renderer::Auto | Renderer::Ascii => {
                        ['#', 'o', '@', '*'][usize::from(*index) % 4]
                    }
                    Renderer::HalfBlock
                        if self.colors.palette.iter().all(|c| *c == Color::Reset) =>
                    {
                        ['#', 'o', '@', '*'][usize::from(*index) % 4]
                    }
                    Renderer::HalfBlock => match (pixels[0], pixels[1]) {
                        (Some(upper), Some(lower)) => {
                            fg = self.colors.ink(usize::from(upper));
                            bg = self.colors.ink(usize::from(lower));
                            // Identical pixels use foreground coverage throughout the cell;
                            // translucent terminal backgrounds must not add scan lines.
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
                if let Some(cell) = buf.cell_mut(((left + col) as u16, (top + row) as u16)) {
                    cell.set_char(symbol).set_fg(fg).set_bg(bg);
                }
            }
        }
        // Literal ASCII faces stay on the projected head, never on a rear view
        // or outside a clipped/tiny sprite. Small views retain the HUD face.
        if let Some(face) = self.sprite.face_overlay() {
            let length = face.text.len() as u32;
            let center = u32::from(face.center_x) * u32::from(scale) / u32::from(dx);
            let row = u32::from(face.y) * u32::from(scale) / u32::from(dy);
            let Some(start) = center.checked_sub(length / 2) else {
                return;
            };
            if length == 0
                || !face.text.is_ascii()
                || face.text.bytes().any(|c| c.is_ascii_control())
                || start.saturating_add(length) > columns
                || row >= rows
            {
                return;
            }
            let covered = (start..start + length).all(|col| {
                (0..dy).any(|y| {
                    (0..dx).any(|x| {
                        self.sprite
                            .pixel(
                                col * u32::from(dx) + u32::from(x),
                                row * u32::from(dy) + u32::from(y),
                                scale,
                            )
                            .is_some()
                    })
                })
            });
            if covered {
                for (offset, glyph) in face.text.chars().enumerate() {
                    if let Some(cell) =
                        buf.cell_mut(((left + start + offset as u32) as u16, (top + row) as u16))
                    {
                        cell.set_char(glyph)
                            .set_fg(self.colors.ink(0))
                            .set_bg(self.colors.ink(4));
                    }
                }
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
pub struct RoomWidget<'a> {
    pub sprite: &'a Sprite,
    pub renderer: Renderer,
    pub scale: u16,
    pub colors: &'a Colors,
    pub expression: crate::sprite::Expression,
    pub frame: usize,
    pub position: (u16, u16),
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
        let wall = self.colors.ink(if self.expression == Expression::Sleeping {
            6
        } else {
            10
        });
        for y in area.y..area.bottom() {
            for x in area.x..area.right() {
                let ground = y >= floor;
                let bg = if ground {
                    self.colors
                        .ink(if ((x - area.x) / 8 + (y - floor)).is_multiple_of(2) {
                            14
                        } else {
                            6
                        })
                } else {
                    wall
                };
                if let Some(cell) = buf.cell_mut((x, y)) {
                    cell.set_char(
                        if !ground && (x - area.x).is_multiple_of(7) && (y - area.y) % 3 == 1 {
                            if ascii {
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
        if area.width >= 28 && area.height >= 10 {
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
        let margin = if area.height >= 12 { 1 } else { 0 };
        let available_height = area.height.saturating_sub(2 * margin).max(1);
        let available_width = (area.width / 2).max(1);
        let (dx, dy) = self.renderer.packing();
        let compact = self.sprite.downsample_to_fit(
            available_width.saturating_mul(dx),
            available_height.saturating_mul(dy),
        );
        let sprite = &compact;
        let scale = fit_scale(
            sprite.width(),
            sprite.height(),
            available_width,
            available_height,
            self.renderer,
            self.scale,
        )
        .unwrap_or(1);
        let (columns, rows) = cell_size(sprite.width(), sprite.height(), scale, self.renderer);
        let columns = (columns as u16).min(area.width);
        let rows = (rows as u16).min(available_height);
        let left = area.x
            + ((u32::from(area.width.saturating_sub(columns))
                * u32::from(self.position.0.min(1000)))
                / 1000) as u16;
        let top = area.y
            + margin
            + ((u32::from(available_height.saturating_sub(rows))
                * u32::from(self.position.1.min(1000)))
                / 1000) as u16;
        let body = Rect::new(left, top, columns, rows);
        // A small floor shadow anchors the volume without needing a graphics GPU.
        let shadow_y = body.bottom().min(area.bottom().saturating_sub(1));
        for x in left..left.saturating_add(columns).min(area.right()) {
            if let Some(cell) = buf.cell_mut((x, shadow_y)) {
                cell.set_char(if ascii { '.' } else { '▄' })
                    .set_fg(self.colors.ink(6));
            }
        }
        SpriteWidget {
            sprite,
            renderer: self.renderer,
            scale,
            colors: self.colors,
        }
        .render(body, buf);
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
            let mut buffer = Buffer::empty(Rect::new(0, 0, 100, 100));
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
