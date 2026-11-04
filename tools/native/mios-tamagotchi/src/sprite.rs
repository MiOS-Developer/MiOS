// AI-hint: Validated indexed sprite and overflow-safe viewport fitting in terminal cells.
// AI-related: render.rs, specs/tamagotchi-tui/tamagotchi-sprites-spec.md
use crate::capabilities::Renderer;

#[derive(Clone, Copy, Debug, PartialEq, Eq, serde::Deserialize, clap::ValueEnum)]
#[serde(rename_all = "kebab-case")]
#[value(rename_all = "kebab-case")]
pub enum SpriteStyle {
    EightBit,
    SixteenBit,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Expression {
    Idle,
    Happy,
    Sad,
    Angry,
    Surprised,
    Sleeping,
    Sick,
    Hungry,
    Playful,
    Shy,
    Excited,
    Eating,
    Cleaning,
    Healing,
    Celebrate,
    Dead,
}

#[derive(Clone, Debug)]
pub struct Sprite {
    width: u16,
    height: u16,
    pixels: Vec<Option<u8>>,
}

impl Sprite {
    /// Original seedling pet art. The caller advances `frame` from its animation
    /// clock; this pure four-frame cycle never advances game time or reads a clock.
    /// Idle includes a blink, breathing and alternating walking feet.
    /// The detailed artwork has independent contours, shading, eye highlights,
    /// a cream bib and props, rather than enlarged low-resolution pixels.
    pub fn animated(
        stage: crate::game::Stage,
        expression: Expression,
        style: SpriteStyle,
        frame: usize,
    ) -> Self {
        use crate::game::Stage;
        let expression = if stage == Stage::Dead {
            Expression::Dead
        } else {
            expression
        };
        let stage = if expression == Expression::Dead {
            Stage::Dead
        } else {
            stage
        };
        let rows: &[&str] = match (style, stage) {
            (SpriteStyle::EightBit, Stage::Egg) => &[
                "........", "...00...", "..0430..", ".043340.", ".044440.", ".047440.", "..0000..",
                "........",
            ],
            (SpriteStyle::EightBit, Stage::Baby) => &[
                "........", "........", "...00...", "..0330..", ".011110.", ".014410.", "..0aa0..",
                "...00...",
            ],
            (SpriteStyle::EightBit, Stage::Child) => &[
                "........", "..b.....", "..0000..", ".013310.", ".011110.", ".014410.", "..0aa0..",
                "..0..0..",
            ],
            (SpriteStyle::EightBit, Stage::Adult) => &[
                "..b..b..", "..0bb0..", ".013310.", ".011110.", ".014410.", ".014410.", "..0aa0..",
                ".00..00.",
            ],
            (SpriteStyle::EightBit, Stage::Dead) => &[
                "..7777..", "........", "..0000..", ".023320.", ".020020.", ".023320.", ".030030.",
                "..0..0..",
            ],
            (SpriteStyle::SixteenBit, Stage::Egg) => &[
                "................",
                "................",
                "......0000......",
                ".....043330.....",
                "....04322330....",
                "...0433333330...",
                "...0433734330...",
                "..043337743330..",
                "..043333333330..",
                "..043343343330..",
                "..0434444443a0..",
                "...04447444a0...",
                "...0447744aa0...",
                "....04aaaaa0....",
                ".....000000.....",
                "................",
            ],
            (SpriteStyle::SixteenBit, Stage::Baby) => &[
                "................",
                "................",
                "................",
                "................",
                "......0000......",
                "....00333300....",
                "...0333223330...",
                "..013333333310..",
                "..013333333310..",
                "..011344443110..",
                "..011444444110..",
                "...0144444410...",
                "....01aaaa10....",
                ".....000000.....",
                "....00....00....",
                "................",
            ],
            (SpriteStyle::SixteenBit, Stage::Child) => &[
                "................",
                ".....bb.........",
                "......b.........",
                ".....000000.....",
                "....03333330....",
                "...0333223330...",
                "...0133333310...",
                "..013333333310..",
                "..013333333310..",
                "..011344443110..",
                "...0144444410...",
                "...0144444a10...",
                "....01aaaa10....",
                ".....000000.....",
                "....0ee..ee0....",
                ".....00..00.....",
            ],
            (SpriteStyle::SixteenBit, Stage::Adult) => &[
                "....bb....bb....",
                ".....bf..fb.....",
                "......0bb0......",
                "....00333300....",
                "...0333223330...",
                "..013333333310..",
                "..013333333310..",
                "..013333333310..",
                ".0313333331130..",
                ".0a113444311a0..",
                "..011444444110..",
                "...0144444a10...",
                "...014444aa10...",
                "....01aaaa10....",
                "...00e0..0e00...",
                "...0ee0..0ee0...",
            ],
            (SpriteStyle::SixteenBit, Stage::Dead) => &[
                "......7777......",
                ".....7....7.....",
                "......7777......",
                "................",
                ".....000000.....",
                "....02333320....",
                "...0233333320...",
                "...0233333320...",
                "...0233333320...",
                "...0233333320...",
                "...0233333320...",
                "...0233333320...",
                "...0233333320...",
                "...0300330030...",
                "....0..00..0....",
                "................",
            ],
        };
        let size = rows.len() as u16;
        let mut sprite = Self {
            width: size,
            height: size,
            pixels: rows
                .iter()
                .flat_map(|row| row.bytes())
                .map(|c| match c {
                    b'0'..=b'9' => Some(c - b'0'),
                    b'a'..=b'f' => Some(c - b'a' + 10),
                    _ => None,
                })
                .collect(),
        };
        let phase = frame % 4;
        match style {
            SpriteStyle::EightBit => sprite.animate_small(stage, expression, phase),
            SpriteStyle::SixteenBit => sprite.animate_detailed(stage, expression, phase),
        }
        sprite
    }

    // All drawing is clipped in signed coordinates before the row-major lookup.
    fn paint(&mut self, x: i32, y: i32, color: u8) {
        if x >= 0 && y >= 0 && x < i32::from(self.width) && y < i32::from(self.height) {
            self.pixels[y as usize * usize::from(self.width) + x as usize] = Some(color);
        }
    }

    fn stamp(&mut self, x: i32, y: i32, rows: &[&str]) {
        for (dy, row) in rows.iter().enumerate() {
            for (dx, byte) in row.bytes().enumerate() {
                let color = match byte {
                    b'0'..=b'9' => byte - b'0',
                    b'a'..=b'f' => byte - b'a' + 10,
                    _ => continue,
                };
                self.paint(x + dx as i32, y + dy as i32, color);
            }
        }
    }

    fn animate_small(&mut self, stage: crate::game::Stage, expression: Expression, phase: usize) {
        use crate::game::Stage;
        use Expression::*;
        let p = phase as i32;
        if expression == Dead {
            // Crossed eyes, a scalloped ghost hem and a drifting halo glint.
            self.stamp(2, 3, &["0..0", ".00.", "0..0"]);
            self.paint(2 + p, 0, 2);
            self.paint(p, 7, 13);
            return;
        }
        let eye_y = if matches!(stage, Stage::Baby | Stage::Child) {
            4
        } else {
            3
        };
        let blink = expression == Idle && phase == 2;
        let eye = if blink || matches!(expression, Sleeping | Sick | Sad | Shy | Angry) {
            0
        } else {
            2
        };
        self.paint(2, eye_y, eye);
        self.paint(5, eye_y, eye);
        self.paint(3, 5, 6);
        self.paint(4, 5, 6);
        match expression {
            Idle => {
                // Four distinct footfalls; the third pose also blinks.
                if stage == Stage::Egg {
                    self.paint(2 + p, 6, 7); // shell rocks along its lower rim
                } else {
                    self.paint(1 + p, 7, 0);
                    self.paint(6 - p, 6, 3);
                }
            }
            Happy => {
                self.stamp(2, eye_y, &["0..0"]);
                self.paint(3, 5, 5);
                self.stamp(p, 0, &["c.c", ".c."]);
            }
            Sad => {
                self.paint(3, 4, 6);
                self.paint(4, 5, 1);
                self.paint(1, 3 + p, 9); // falling tear
            }
            Angry => {
                self.paint(3, eye_y - 1, 6);
                self.paint(4, eye_y - 1, 6);
                self.paint(7, p, 12); // pulsing anger mark
                self.paint(6, p + 1, 12);
            }
            Surprised => {
                self.stamp(3, 5, &["00"]);
                self.stamp(p, 0, &["7", "6", "7"]);
            }
            Sleeping => {
                self.stamp(2, eye_y, &["6..6"]);
                self.paint(4, 5, 1);
                // A sleep bubble drifts up the right side in four poses.
                self.paint(7, 4 - p, 13);
                self.paint(6, 3 - p, 2);
            }
            Sick => {
                self.stamp(3, 5, &["6a"]);
                self.paint(6, 2 + p, 11); // queasy sweat
                self.paint(1, eye_y + 1, 15);
            }
            Hungry => {
                self.stamp(3, 5, &["60"]);
                self.stamp(p, 0, &["8b"]); // food thought traverses overhead
                self.paint(3, 6, if phase.is_multiple_of(2) { 4 } else { 14 });
            }
            Playful => {
                self.paint(5, eye_y, 0);
                self.paint(0, 4 - (p % 2), 1);
                self.stamp(5 + (p % 2), 6 - (p / 2), &["d8", "8d"]);
            }
            Shy => {
                self.paint(2, eye_y + 1, 5);
                self.paint(5, eye_y + 1, 5);
                self.paint(2 + p, 5, 3); // hands hide the smile
            }
            Excited => {
                self.paint(2, eye_y, 7);
                self.paint(5, eye_y, 7);
                self.paint(0, 4 - p, 1);
                self.stamp(6, p, &["7", "27"]);
            }
            Eating => {
                self.stamp(
                    3,
                    5,
                    if phase.is_multiple_of(2) {
                        &["60"]
                    } else {
                        &["66"]
                    },
                );
                // Leaf, fruit, bite, then crumb: visible consumption, not a blink.
                match phase {
                    0 => self.stamp(6, 3, &["b.", "88", "88"]),
                    1 => self.stamp(6, 4, &["b.", "88"]),
                    2 => self.stamp(5, 5, &["8"]),
                    _ => self.paint(6, 6, 7),
                }
            }
            Cleaning => {
                self.paint(0, 5 - p, 9);
                self.paint(7, p, 13);
                self.stamp(p, 6, &["ff", "99"]);
            }
            Healing => {
                self.stamp(5, 0, &[".c.", "ccc", ".c."]);
                self.paint(1, 3 + p, 15);
                self.stamp(4, 5, &["45"]);
            }
            Celebrate => {
                self.paint(p, 0, 7);
                self.paint(7 - p, 1, 12);
                self.paint(0, 3 + (p % 2), 1);
                self.paint(7, 4 - (p % 2), 1);
                self.paint(3, 5, 5);
            }
            Dead => unreachable!(),
        }
    }

    fn animate_detailed(
        &mut self,
        stage: crate::game::Stage,
        expression: Expression,
        phase: usize,
    ) {
        use crate::game::Stage;
        use Expression::*;
        let p = phase as i32;
        if expression == Dead {
            self.stamp(4, 6, &["0.0..0.0", ".0....0.", "0.0..0.0"]);
            self.stamp(7, 10, &["66"]);
            self.paint(6 + p, 0, 2);
            self.stamp(p, 13 - (p % 2), &["dd", ".d"]);
            return;
        }
        let eye_y = match stage {
            Stage::Baby => 8,
            Stage::Child => 7,
            _ => 6,
        };
        // Two-tone whites, dark pupils and one-pixel catchlights are independent
        // of the small sprite's single-pixel eyes.
        let closed = matches!(expression, Sleeping | Shy) || (expression == Idle && phase == 2);
        let eyes: &[&str] = if closed {
            &["......", "000000"]
        } else {
            &["220022", "200002", "000000"]
        };
        // Draw eyes separately so the bridge retains the body/shell color.
        if closed {
            self.stamp(4, eye_y + 1, &["000..000"]);
        } else {
            self.stamp(4, eye_y, &[&eyes[0][..2], &eyes[1][..2], &eyes[2][..2]]);
            self.stamp(10, eye_y, &[&eyes[0][4..], &eyes[1][4..], &eyes[2][4..]]);
        }
        self.stamp(3, eye_y + 2, &["55......55"]);
        let mouth_y = eye_y + 3;
        self.stamp(7, mouth_y, &["66"]);
        match expression {
            Idle => {
                if stage == Stage::Egg {
                    self.stamp(5 + p, 12, &["7a", "a7"]);
                } else {
                    self.stamp(3 + p, 14, &["0e0", "000"]);
                    self.stamp(10 - p, 13, &["030", ".0."]);
                    self.paint(7 + p, 11, 3); // breathing bib highlight
                }
            }
            Happy => {
                self.stamp(4, eye_y, &[".0....0.", "0.0..0.0"]);
                self.stamp(6, mouth_y, &["6..6", ".55."]);
                self.stamp(p, 1, &["c.c", "ccc", ".c."]);
            }
            Sad => {
                self.stamp(4, eye_y - 1, &["00....00"]);
                self.stamp(6, mouth_y, &[".66.", "6..6"]);
                self.stamp(2, eye_y + p, &["d", "9", "d"]);
            }
            Angry => {
                self.stamp(4, eye_y - 1, &["60....06", ".6....6."]);
                self.stamp(6, mouth_y, &["6666"]);
                self.stamp(12, p, &["c.c", ".c.", "c.c"]);
            }
            Surprised => {
                self.stamp(6, mouth_y - 1, &[".00.", "0660", ".00."]);
                self.stamp(p, 0, &["77", "77", "..", "66"]);
            }
            Sleeping => {
                self.stamp(7, mouth_y, &["6", ".6"]);
                self.stamp(12, 5 - p, &["dd", "d2"]);
                self.stamp(12 - p, 0, &["777", "..7", ".7.", "777"]);
                self.paint(7 + p, 11, 3);
            }
            Sick => {
                self.stamp(4, eye_y, &["0.0..0.0", ".0....0.", "0.0..0.0"]);
                self.stamp(6, mouth_y, &["6.6.", ".6.6"]);
                self.stamp(1, 5 + p, &["f", "bb", "f"]);
                self.stamp(12, eye_y + 2, &["af"]);
            }
            Hungry => {
                self.stamp(6, mouth_y, &["0660", ".00."]);
                self.stamp(2 + p, 0, &[".b.", "888", "8e8", ".8."]);
                self.stamp(
                    6,
                    12,
                    if phase.is_multiple_of(2) {
                        &["4ee4"]
                    } else {
                        &["e44e"]
                    },
                );
            }
            Playful => {
                self.stamp(10, eye_y, &["00", ".0"]);
                self.stamp(0, 7 - (p % 2), &["03", "0a"]);
                self.stamp(
                    11 + (p % 2),
                    12 - (p / 2),
                    &[".00.", "0d80", "08d0", ".00."],
                );
            }
            Shy => {
                self.stamp(3, eye_y + 2, &["555.....555"]);
                self.stamp(5 + p, mouth_y, &["0330", ".aa."]);
                self.paint(12, 4 + p, 5);
            }
            Excited => {
                self.stamp(4, eye_y, &["77....77", "72....27"]);
                self.stamp(6, mouth_y, &["6006", ".55."]);
                self.stamp(0, 7 - p, &["03", "0a"]);
                self.stamp(12, p, &[".7.", "727", ".7."]);
            }
            Eating => {
                self.stamp(
                    6,
                    mouth_y,
                    if phase.is_multiple_of(2) {
                        &["6006", ".55."]
                    } else {
                        &["6666", ".66."]
                    },
                );
                match phase {
                    0 => self.stamp(12, 7, &[".b.", "888", "8e8", ".8."]),
                    1 => self.stamp(11, 8, &[".b.", "888", ".e8"]),
                    2 => self.stamp(10, 9, &["88", ".e"]),
                    _ => self.stamp(11, 12, &["7.7", ".7."]),
                }
                self.stamp(10, 11, &["030"]);
            }
            Cleaning => {
                self.stamp(0, 8 - p, &[".d.", "d2d", ".9."]);
                self.stamp(12, p, &[".d.", "d2d", ".9."]);
                self.stamp(2 + p, 13, &["ffff", "f22f", "9999"]);
                self.paint(10 + p, 15, 9);
            }
            Healing => {
                self.stamp(11, 0, &[".ccc.", ".c2c.", "cc2cc", ".ccc.", ".ccc."]);
                self.stamp(10, eye_y + 2, &["44", "5e", "44"]);
                self.stamp(0, 5 + p, &[".f.", "fff", ".f."]);
            }
            Celebrate => {
                self.stamp(0, 2 + p, &["7.7", ".7."]);
                self.stamp(11 - p, 0, &["c.c", "ccc", ".c."]);
                self.stamp(0, 7 - (p % 2), &["03", ".a"]);
                self.stamp(13, 7 + (p % 2), &["30", "a."]);
                self.stamp(6, mouth_y, &["6006", ".55."]);
                self.paint(2 + p * 3, 15, 7);
            }
            Dead => unreachable!(),
        }
    }

    pub fn width(&self) -> u16 {
        self.width
    }
    pub fn height(&self) -> u16 {
        self.height
    }
    pub fn new(width: u16, height: u16, pixels: Vec<Option<u8>>) -> Result<Self, String> {
        if width == 0 || height == 0 || width > 256 || height > 256 {
            return Err("sprite dimensions must be in 1..=256".into());
        }
        if pixels.len() != usize::from(width) * usize::from(height) {
            return Err("sprite pixel count does not match its dimensions".into());
        }
        if pixels.iter().flatten().any(|i| *i >= 16) {
            return Err("sprite palette indices must be in 0..16".into());
        }
        Ok(Self {
            width,
            height,
            pixels,
        })
    }

    pub fn pixel(&self, x: u32, y: u32, scale: u16) -> Option<u8> {
        if scale == 0 {
            return None;
        }
        let (x, y) = (x / u32::from(scale), y / u32::from(scale));
        if x >= u32::from(self.width) || y >= u32::from(self.height) {
            return None;
        }
        self.pixels[(y * u32::from(self.width) + x) as usize]
    }

    pub fn pet(stage: crate::game::Stage, frame: usize) -> Self {
        use crate::game::Stage;
        let rows = match stage {
            Stage::Egg => return Self::egg(),
            Stage::Adult => return Self::preview(frame),
            Stage::Baby => [
                "........", "........", "...00...", "..0110..", "..0220..", "..0110..", "...00...",
                "........",
            ],
            Stage::Child => [
                "........", "..0..0..", ".011110.", ".021120.", ".011110.", "..0110..", "..0..0..",
                "........",
            ],
            Stage::Dead => [
                "........", "..0000..", ".022220.", ".020020.", ".022220.", "..0000..", "..0..0..",
                "........",
            ],
        };
        let pixels = rows
            .iter()
            .flat_map(|r| r.bytes())
            .map(|c| (c != b'.').then(|| c - b'0'))
            .collect();
        Self {
            width: 8,
            height: 8,
            pixels,
        }
    }

    pub fn egg() -> Self {
        let rows = [
            "........", "...00...", "..0330..", ".031130.", ".011110.", ".011110.", "..0000..",
            "........",
        ];
        let pixels = rows
            .iter()
            .flat_map(|r| r.bytes())
            .map(|c| (c != b'.').then(|| c - b'0'))
            .collect();
        Self {
            width: 8,
            height: 8,
            pixels,
        }
    }

    /// Original local-pet art, with two animation frames.
    pub fn preview(frame: usize) -> Self {
        let rows = [
            "..0000..", ".011110.", "01133110", "01211210", "01111110", ".011110.", "..0000..",
            ".00..00.",
        ];
        let mut pixels: Vec<_> = rows
            .iter()
            .flat_map(|row| row.bytes())
            .map(|c| (c != b'.').then(|| c - b'0'))
            .collect();
        if frame % 2 == 1 {
            pixels[3 * 8 + 2] = Some(0);
            pixels[3 * 8 + 5] = Some(0);
        }
        Self {
            width: 8,
            height: 8,
            pixels,
        }
    }
}

pub fn cell_size(width: u16, height: u16, scale: u16, renderer: Renderer) -> (u32, u32) {
    let (dx, dy) = renderer.packing();
    (
        (u32::from(width) * u32::from(scale)).div_ceil(u32::from(dx)),
        (u32::from(height) * u32::from(scale)).div_ceil(u32::from(dy)),
    )
}

/// `columns` and `rows` are the actual drawable viewport, with UI chrome already removed.
/// Zero means automatic scale; a requested fixed scale is never silently changed.
pub fn fit_scale(
    width: u16,
    height: u16,
    columns: u16,
    rows: u16,
    renderer: Renderer,
    requested: u16,
) -> Option<u16> {
    if width == 0 || height == 0 {
        return None;
    }
    [16, 8, 4, 2, 1].into_iter().find(|&scale| {
        let (w, h) = cell_size(width, height, scale, renderer);
        (requested == 0 || requested == scale) && w <= u32::from(columns) && h <= u32::from(rows)
    })
}

#[cfg(test)]
mod animation_tests {
    use super::*;
    use crate::game::Stage;

    const STYLES: [SpriteStyle; 2] = [SpriteStyle::EightBit, SpriteStyle::SixteenBit];
    const STAGES: [Stage; 5] = [
        Stage::Egg,
        Stage::Baby,
        Stage::Child,
        Stage::Adult,
        Stage::Dead,
    ];
    const EXPRESSIONS: [Expression; 16] = [
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
    ];

    #[test]
    fn palette_and_geometry_validation_has_positive_and_negative_controls() {
        for index in 0..16 {
            assert_eq!(
                Sprite::new(1, 1, vec![Some(index)]).unwrap().pixel(0, 0, 1),
                Some(index)
            );
        }
        for index in [16, 17, u8::MAX] {
            assert!(Sprite::new(1, 1, vec![Some(index)])
                .unwrap_err()
                .contains("palette"));
        }
        assert!(Sprite::new(256, 256, vec![Some(15); 65536]).is_ok());
        for (width, height) in [(0, 1), (1, 0), (257, 1), (1, 257), (u16::MAX, u16::MAX)] {
            assert!(Sprite::new(width, height, vec![]).is_err());
        }
        assert!(Sprite::new(2, 2, vec![None; 3]).is_err());
        assert!(Sprite::new(2, 2, vec![None; 5]).is_err());
    }

    #[test]
    fn every_style_stage_reaction_and_frame_is_populated_bounded_and_periodic() {
        for style in STYLES {
            let size = if style == SpriteStyle::EightBit {
                8
            } else {
                16
            };
            for stage in STAGES {
                for expression in EXPRESSIONS {
                    for frame in [0, 1, 2, 3, 4, 7, usize::MAX] {
                        let sprite = Sprite::animated(stage, expression, style, frame);
                        assert_eq!((sprite.width(), sprite.height()), (size, size));
                        assert_eq!(sprite.pixels.len(), usize::from(size * size));
                        assert!(sprite.pixels.iter().flatten().count() >= 16);
                        assert!(sprite.pixels.contains(&None), "transparent surroundings");
                        assert!(sprite.pixels.iter().flatten().all(|&color| color < 16));
                        assert_eq!(sprite.pixel(u32::MAX, u32::MAX, u16::MAX), None);
                        assert_eq!(sprite.pixel(0, 0, 0), None);
                        assert_eq!(sprite.pixel(u32::from(size), 0, 1), None);
                        assert_eq!(sprite.pixel(0, u32::from(size), 1), None);
                        assert_eq!(
                            sprite.pixels,
                            Sprite::animated(stage, expression, style, frame % 4).pixels
                        );
                        // Revalidate every generated image through the public constructor.
                        assert!(Sprite::new(size, size, sprite.pixels).is_ok());
                    }
                }
            }
        }
    }

    #[test]
    fn every_reaction_has_four_distinct_poses_in_both_styles() {
        for style in STYLES {
            for expression in EXPRESSIONS {
                let frames: Vec<_> = (0..4)
                    .map(|frame| Sprite::animated(Stage::Adult, expression, style, frame).pixels)
                    .collect();
                for left in 0..4 {
                    for right in left + 1..4 {
                        assert_ne!(
                            frames[left], frames[right],
                            "{style:?} {expression:?}: {left}/{right}"
                        );
                    }
                }
            }
        }
    }

    #[test]
    fn reactions_and_growth_change_the_art_and_dead_stage_stays_dead() {
        for style in STYLES {
            for (index, expression) in EXPRESSIONS.iter().enumerate() {
                let image = Sprite::animated(Stage::Adult, *expression, style, 0);
                for other in &EXPRESSIONS[index + 1..] {
                    assert_ne!(
                        image.pixels,
                        Sprite::animated(Stage::Adult, *other, style, 0).pixels,
                        "{style:?}: {expression:?}/{other:?}"
                    );
                }
            }
            for (index, stage) in STAGES.iter().enumerate() {
                let image = Sprite::animated(*stage, Expression::Idle, style, 0);
                for other in &STAGES[index + 1..] {
                    assert_ne!(
                        image.pixels,
                        Sprite::animated(*other, Expression::Idle, style, 0).pixels
                    );
                }
            }
            for expression in EXPRESSIONS {
                assert_eq!(
                    Sprite::animated(Stage::Dead, expression, style, 1).pixels,
                    Sprite::animated(Stage::Dead, Expression::Dead, style, 1).pixels,
                    "death must not render care or happy faces",
                );
            }
        }
    }

    #[test]
    fn detailed_art_is_independent_and_contains_subpixel_detail() {
        for stage in STAGES {
            let small = Sprite::animated(stage, Expression::Idle, SpriteStyle::EightBit, 0);
            let detailed = Sprite::animated(stage, Expression::Idle, SpriteStyle::SixteenBit, 0);
            let enlarged: Vec<_> = (0..16)
                .flat_map(|y| {
                    let small = &small;
                    (0..16).map(move |x| small.pixel(x / 2, y / 2, 1))
                })
                .collect();
            assert_ne!(enlarged, detailed.pixels, "{stage:?}");
            assert!(
                (0..8).any(|y| (0..8).any(|x| {
                    let top = detailed.pixel(x * 2, y * 2, 1);
                    top != detailed.pixel(x * 2 + 1, y * 2, 1)
                        || top != detailed.pixel(x * 2, y * 2 + 1, 1)
                })),
                "independent one-pixel features for {stage:?}"
            );
        }
        let detailed = Sprite::animated(Stage::Adult, Expression::Idle, SpriteStyle::SixteenBit, 0);
        for color in [0, 1, 2, 3, 4, 5, 6, 10, 11, 14, 15] {
            assert!(
                detailed.pixels.contains(&Some(color)),
                "missing detailed palette color {color}"
            );
        }
    }

    #[test]
    fn drawing_clips_and_scaled_pixels_remain_safe() {
        let mut sprite = Sprite::new(1, 1, vec![None]).unwrap();
        sprite.stamp(-1, -1, &["ff", "f7"]);
        assert_eq!(sprite.pixel(0, 0, 1), Some(7));
        sprite.paint(i32::MAX, i32::MIN, 15);
        assert_eq!(sprite.pixel(15, 15, 16), Some(7));
        assert_eq!(sprite.pixel(16, 16, 16), None);
    }

    #[test]
    fn legacy_preview_and_pet_pixels_remain_unchanged() {
        let egg = Sprite::egg();
        let preview = Sprite::preview(0);
        let blink = Sprite::preview(1);
        assert_eq!(egg.pixel(3, 2, 1), Some(3));
        assert_eq!(preview.pixel(2, 3, 1), Some(2));
        assert_eq!(blink.pixel(2, 3, 1), Some(0));
        assert_eq!(preview.pixel(5, 3, 1), Some(2));
        assert_eq!(blink.pixel(5, 3, 1), Some(0));
        assert_eq!(Sprite::pet(Stage::Egg, 1).pixels, egg.pixels);
        assert_eq!(Sprite::pet(Stage::Adult, 0).pixels, preview.pixels);
        for stage in STAGES {
            let legacy = Sprite::pet(stage, 0);
            assert_eq!((legacy.width(), legacy.height()), (8, 8));
            assert!(legacy.pixels.iter().flatten().all(|&color| color < 4));
        }
    }

    #[test]
    fn style_names_match_cli_and_configuration() {
        use clap::ValueEnum;
        for (name, style) in [
            ("eight-bit", SpriteStyle::EightBit),
            ("sixteen-bit", SpriteStyle::SixteenBit),
        ] {
            assert_eq!(SpriteStyle::from_str(name, false).unwrap(), style);
            assert_eq!(
                serde_json::from_str::<SpriteStyle>(&format!("\"{name}\"")).unwrap(),
                style
            );
        }
        assert!(serde_json::from_str::<SpriteStyle>("\"sixteen_bit\"").is_err());
    }
}
