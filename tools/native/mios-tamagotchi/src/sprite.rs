// AI-hint: Indexed legacy sprites, solid CPU voxel rasterization and safe viewport fitting.
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
    /// CPU-only solid voxel seedling. Yaw is periodic; movement is supplied by
    /// the simulation, never inferred from controller input in the renderer.
    pub fn voxel(
        stage: crate::game::Stage,
        expression: Expression,
        style: SpriteStyle,
        frame: usize,
        yaw_degrees: u16,
        walking: bool,
    ) -> Self {
        use crate::game::Stage;
        let dead = stage == Stage::Dead || expression == Expression::Dead;
        let stage = if dead { Stage::Dead } else { stage };
        let expression = if dead { Expression::Dead } else { expression };
        let model = VoxelModel::pet(stage, expression, style, frame % 4, walking);
        model.rasterize(style, yaw_degrees, expression == Expression::Sleeping)
    }

    /// Fit using one rational scale and nearest-neighbor pixel centers. Integer
    /// rounding costs at most one pixel per dimension; this never enlarges art.
    /// An empty target area has a safe transparent sentinel.
    pub fn downsample_to_fit(&self, max_width: u16, max_height: u16) -> Self {
        if max_width == 0 || max_height == 0 || self.width == 0 || self.height == 0 {
            return Self {
                width: 1,
                height: 1,
                pixels: vec![None],
            };
        }
        if self.width <= max_width && self.height <= max_height {
            return self.clone();
        }
        let (sw, sh) = (u32::from(self.width), u32::from(self.height));
        let (mw, mh) = (u32::from(max_width), u32::from(max_height));
        let (numerator, denominator) = if mw * sh <= mh * sw {
            (mw, sw)
        } else {
            (mh, sh)
        };
        let width = (sw * numerator / denominator).max(1) as u16;
        let height = (sh * numerator / denominator).max(1) as u16;
        let mut pixels = Vec::with_capacity(usize::from(width) * usize::from(height));
        for y in 0..u32::from(height) {
            let sy = ((2 * y + 1) * sh / (2 * u32::from(height))).min(sh - 1);
            for x in 0..u32::from(width) {
                let sx = ((2 * x + 1) * sw / (2 * u32::from(width))).min(sw - 1);
                pixels.push(self.pixel(sx, sy, 1));
            }
        }
        Self {
            width,
            height,
            pixels,
        }
    }

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

// A sparse integer lattice contains entire solid ellipsoids, including their
// hidden interiors. Only exposed cube faces are projected. Front relief and the
// rear seed seam therefore disappear naturally behind the body when it turns.
#[derive(Default)]
struct VoxelModel {
    cells: std::collections::BTreeMap<(i32, i32, i32), u8>,
}

impl VoxelModel {
    fn ellipsoid(&mut self, center: [f32; 3], radii: [f32; 3], color: u8) {
        let [cx, cy, cz] = center;
        let [rx, ry, rz] = radii;
        for x in (cx - rx).floor() as i32..=(cx + rx).ceil() as i32 {
            for y in (cy - ry).floor() as i32..=(cy + ry).ceil() as i32 {
                for z in (cz - rz).floor() as i32..=(cz + rz).ceil() as i32 {
                    let d = ((x as f32 - cx) / rx).powi(2)
                        + ((y as f32 - cy) / ry).powi(2)
                        + ((z as f32 - cz) / rz).powi(2);
                    if d <= 1.0 {
                        self.cells.insert((x, y, z), color);
                    }
                }
            }
        }
    }

    fn dot(&mut self, x: i32, y: i32, color: u8) {
        if let Some(z) = self
            .cells
            .keys()
            .filter(|&&(vx, vy, _)| vx == x && vy == y)
            .map(|&(_, _, z)| z)
            .max()
        {
            self.cells.insert((x, y, z + 1), color);
        }
    }

    fn pet(
        stage: crate::game::Stage,
        expression: Expression,
        style: SpriteStyle,
        phase: usize,
        walking: bool,
    ) -> Self {
        use crate::game::Stage;
        use Expression::*;
        let detailed = style == SpriteStyle::SixteenBit;
        let mut model = Self::default();
        let p = phase as f32;
        let (rx, ry, rz) = match (detailed, stage) {
            (false, Stage::Egg) => (4.0, 5.0, 3.0),
            (false, Stage::Baby) => (3.0, 2.8, 2.5),
            (false, Stage::Child) => (4.0, 3.8, 3.0),
            (false, _) => (4.4, 4.5, 3.5),
            (true, Stage::Egg) => (5.5, 7.0, 4.5),
            (true, Stage::Baby) => (4.5, 4.0, 3.5),
            (true, Stage::Child) => (5.5, 5.5, 4.0),
            (true, _) => (6.2, 6.5, 4.8),
        };
        let dead = stage == Stage::Dead;
        let egg = stage == Stage::Egg;
        let body = if egg {
            4
        } else if dead {
            15
        } else if expression == Sick {
            11
        } else {
            1
        };
        model.ellipsoid([0.0, 0.0, 0.0], [rx, ry, rz], body);
        // A rounded upper crown makes an egg, rather than a symmetric ball.
        if egg {
            model.ellipsoid([0.0, ry * 0.35, 0.0], [rx * 0.72, ry * 0.8, rz * 0.8], 4);
            for (x, y) in [(-2, 2), (1, -2), (2, 3), (-1, -4)] {
                model.dot(x, y, if detailed { 7 } else { 11 });
            }
            // Rear shell speckles are volumetric relief too.
            model.ellipsoid([1.0, 1.0, -rz + 0.4], [1.3, 1.6, 1.0], 11);
        } else {
            if !dead {
                // Cream bib, rear seed seam and a round tail establish front/back.
                model.ellipsoid(
                    [0.0, -ry * 0.36, rz * 0.72],
                    [rx * 0.57, ry * 0.52, rz * 0.39],
                    4,
                );
                model.ellipsoid([0.0, -1.0, -rz], [1.0, ry * 0.58, 0.8], 11);
                model.ellipsoid([0.0, -ry * 0.55, -rz - 0.6], [1.6, 1.5, 1.6], 11);
                if detailed {
                    model.ellipsoid([0.0, -ry * 0.55, -rz - 1.4], [0.6, 0.6, 1.0], 7);
                }
                let raised = matches!(expression, Happy | Playful | Excited | Celebrate);
                let hand_y = if raised { 1.5 + (p % 2.0) } else { -1.8 };
                for sign in [-1.0, 1.0] {
                    let front = if matches!(expression, Eating | Shy) {
                        rz * 0.85
                    } else {
                        0.5
                    };
                    let side = if expression == Shy { rx * 0.65 } else { rx };
                    model.ellipsoid([sign * side, hand_y, front], [1.2, 1.8, 1.4], 1);
                }
                // Feet alternate in both height and depth: a genuine 3D gait.
                let gait = if walking && !matches!(expression, Sleeping | Sick) {
                    [0.0, 1.0, 0.0, -1.0][phase]
                } else {
                    0.0
                };
                for sign in [-1.0, 1.0] {
                    model.ellipsoid(
                        [
                            sign * rx * 0.52,
                            -ry - 0.5 + (gait * sign).max(0.0),
                            0.5 + gait * sign * 1.7,
                        ],
                        [
                            if detailed { 1.6 } else { 1.0 },
                            0.9,
                            if detailed { 2.0 } else { 1.3 },
                        ],
                        14,
                    );
                }
                if stage != Stage::Baby {
                    // Growing leaves/ears are separate rounded volumes, not cards.
                    model.ellipsoid([-1.0, ry + 0.8, 0.0], [0.8, 1.8, 0.8], 11);
                    model.ellipsoid([-2.0, ry + 1.6, 0.0], [1.8, 0.9, 1.2], 11);
                    if stage == Stage::Adult {
                        model.ellipsoid([2.0, ry + 1.8, -0.4], [1.4, 1.8, 1.0], 11);
                        for sign in [-1.0, 1.0] {
                            model.ellipsoid([sign * rx * 0.75, ry * 0.8, -0.6], [1.4, 2.0, 1.5], 1);
                        }
                    }
                    if detailed {
                        model.ellipsoid([-2.0, ry + 1.8, 0.6], [1.0, 0.5, 0.6], 3);
                    }
                }
            } else {
                // Ghost scallops and a horizontal 3D halo.
                for x in [-3.0, 0.0, 3.0] {
                    model.ellipsoid([x, -ry + 0.2, 0.0], [1.5, 1.5, 2.0], 15);
                }
                for x in -3..=3 {
                    for z in -2..=2 {
                        let ring = (x * x) as f32 / 9.0 + (z * z) as f32 / 4.0;
                        if (0.5..=1.4).contains(&ring) {
                            model.cells.insert((x, ry as i32 + 3, z), 7);
                        }
                    }
                }
            }
        }
        let eye_y = if egg { 0 } else { (ry * 0.24) as i32 };
        let eye_x = if detailed { 2 } else { 1 };
        for sign in [-1, 1] {
            let x = sign * eye_x;
            match expression {
                Sleeping | Shy => {
                    model.dot(x, eye_y, 0);
                    model.dot(x + sign, eye_y, 0);
                }
                Sick | Dead => {
                    for dy in [-1, 1] {
                        for dx in [-1, 1] {
                            model.dot(x + dx, eye_y + dy, 0);
                        }
                    }
                    model.dot(x, eye_y, 0);
                }
                Happy | Celebrate => {
                    model.dot(x, eye_y + 1, 0);
                    model.dot(x + sign, eye_y, 0);
                }
                _ => {
                    model.dot(x, eye_y, 0);
                    if detailed {
                        model.dot(x, eye_y + 1, if expression == Excited { 7 } else { 2 });
                        model.dot(x + sign, eye_y, 0);
                    }
                    if expression == Idle && phase == 2 {
                        model.dot(x, eye_y + 1, body);
                    }
                }
            }
            let brow = match expression {
                Angry => -sign,
                Sad => sign,
                Surprised => 0,
                _ => 2,
            };
            if brow != 2 {
                model.dot(x, eye_y + 2, 6);
                model.dot(x + sign, eye_y + 2 + brow * sign, 6);
            }
            if !egg && !dead {
                model.dot(x + sign, eye_y - 1, 5);
                if detailed {
                    model.dot(x + 2 * sign, eye_y - 1, 5);
                }
            }
        }
        let my = eye_y - 2;
        model.dot(0, my, 6);
        match expression {
            Surprised | Hungry | Eating => {
                model.dot(
                    0,
                    my - 1,
                    if expression == Eating && phase.is_multiple_of(2) {
                        5
                    } else {
                        0
                    },
                );
                model.dot(-1, my, 6);
                model.dot(1, my, 6);
            }
            Happy | Playful | Excited | Celebrate => {
                model.dot(-1, my + 1, 6);
                model.dot(1, my + 1, 6);
                model.dot(0, my - 1, 5);
            }
            Sad | Angry | Sick | Dead => {
                model.dot(-1, my - 1, 6);
                model.dot(1, my - 1, 6);
            }
            _ => {}
        }
        if !dead {
            let side = rx + 2.0;
            match expression {
                Sleeping => {
                    model.ellipsoid([side, 4.0 + p * 0.6, 0.0], [1.0, 1.0, 1.0], 13);
                    model.ellipsoid([0.0, -ry, -1.0], [rx + 0.6, 1.2, rz], 13);
                }
                Sick => {
                    model.ellipsoid([side, 0.5, rz * 0.5], [0.6, 2.0, 0.6], 2);
                    model.ellipsoid([side, -1.0, rz * 0.5], [0.9, 0.9, 0.9], 12);
                }
                Eating | Hungry => {
                    let bite = if expression == Eating {
                        phase as f32 * 0.3
                    } else {
                        0.0
                    };
                    model.ellipsoid(
                        [side - bite, -1.5 + bite, rz * 0.8],
                        [1.5 - bite * 0.4, 1.5, 1.3],
                        8,
                    );
                    model.ellipsoid([side - bite, 0.2 + bite, rz * 0.8], [0.7, 0.8, 0.7], 11);
                }
                Cleaning => {
                    model.ellipsoid([0.0, -ry, 0.0], [rx + 1.5, 1.6, rz + 0.8], 9);
                    for (x, y, z) in [
                        (-side, p - 1.0, 1.0),
                        (side, 2.0 + p, -1.0),
                        (2.0, -ry + 1.0, rz),
                    ] {
                        model.ellipsoid([x, y, z], [1.0, 1.0, 1.0], 13);
                        if detailed {
                            model.ellipsoid([x - 0.3, y + 0.4, z + 0.6], [0.6, 0.6, 0.6], 2);
                        }
                    }
                }
                Healing => {
                    model.ellipsoid([side, 3.0, 0.0], [0.8, 2.0, 0.8], 12);
                    model.ellipsoid([side, 3.0, 0.0], [1.8, 0.8, 0.8], 12);
                    for x in 1..=3 {
                        model.dot(x, -1, if x == 2 { 5 } else { 4 });
                    }
                }
                Playful => {
                    model.ellipsoid([side, -ry + 1.0 + p % 2.0, 1.0], [1.7, 1.7, 1.7], 8);
                    model.ellipsoid([side, -ry + 1.3 + p % 2.0, 2.3], [0.7, 0.7, 0.7], 13);
                }
                Celebrate | Excited => {
                    for sign in [-1.0, 1.0] {
                        model.ellipsoid(
                            [sign * side, ry * 0.6 + (p + sign).rem_euclid(3.0), 0.0],
                            [0.7, 1.3, 0.7],
                            7,
                        );
                        model.ellipsoid(
                            [sign * side, ry * 0.6 + (p + sign).rem_euclid(3.0), 0.0],
                            [1.3, 0.7, 0.7],
                            12,
                        );
                    }
                }
                Sad => {
                    model.ellipsoid([-rx * 0.6, -1.0 - p * 0.4, rz], [0.6, 1.2, 0.7], 9);
                }
                Shy => {
                    model.ellipsoid([0.0, -2.5, rz + 1.0], [1.3, 1.0, 1.0], 5);
                }
                _ => {}
            }
        }
        model
    }
}

impl VoxelModel {
    fn rasterize(&self, style: SpriteStyle, yaw: u16, sleeping: bool) -> Sprite {
        let size: u16 = if style == SpriteStyle::EightBit {
            24
        } else {
            32
        };
        let mut sprite = Sprite {
            width: size,
            height: size,
            pixels: vec![None; usize::from(size) * usize::from(size)],
        };
        let mut depth = vec![f32::NEG_INFINITY; sprite.pixels.len()];
        let angle = f32::from(yaw % 360).to_radians();
        let (s, c) = angle.sin_cos();
        let pitch: f32 = 0.32;
        let (sp, cp) = pitch.sin_cos();
        let pose: f32 = if sleeping { 0.75 } else { 0.0 };
        let (sr, cr) = pose.sin_cos();
        let transform = |[x, y, z]: [f32; 3]| {
            let (x, y) = (cr * x - sr * y, sr * x + cr * y);
            let (x, z) = (c * x + s * z, -s * x + c * z);
            [x, cp * y - sp * z, sp * y + cp * z]
        };
        let project = |v: [f32; 3]| {
            let [x, y, z] = transform(v);
            [
                f32::from(size) * 0.5 + x,
                f32::from(size) * 0.57 - y + if sleeping { 1.0 } else { 0.0 },
                z,
            ]
        };
        // Unit cube quads. Neighbor culling eliminates interior surfaces; depth
        // interpolation below resolves occlusion between all remaining faces.
        const FACES: [([i32; 3], [[f32; 3]; 4]); 6] = [
            (
                [1, 0, 0],
                [
                    [0.5, -0.5, -0.5],
                    [0.5, 0.5, -0.5],
                    [0.5, 0.5, 0.5],
                    [0.5, -0.5, 0.5],
                ],
            ),
            (
                [-1, 0, 0],
                [
                    [-0.5, -0.5, 0.5],
                    [-0.5, 0.5, 0.5],
                    [-0.5, 0.5, -0.5],
                    [-0.5, -0.5, -0.5],
                ],
            ),
            (
                [0, 1, 0],
                [
                    [-0.5, 0.5, -0.5],
                    [-0.5, 0.5, 0.5],
                    [0.5, 0.5, 0.5],
                    [0.5, 0.5, -0.5],
                ],
            ),
            (
                [0, -1, 0],
                [
                    [-0.5, -0.5, 0.5],
                    [-0.5, -0.5, -0.5],
                    [0.5, -0.5, -0.5],
                    [0.5, -0.5, 0.5],
                ],
            ),
            (
                [0, 0, 1],
                [
                    [-0.5, -0.5, 0.5],
                    [0.5, -0.5, 0.5],
                    [0.5, 0.5, 0.5],
                    [-0.5, 0.5, 0.5],
                ],
            ),
            (
                [0, 0, -1],
                [
                    [0.5, -0.5, -0.5],
                    [-0.5, -0.5, -0.5],
                    [-0.5, 0.5, -0.5],
                    [0.5, 0.5, -0.5],
                ],
            ),
        ];
        for (&(x, y, z), &material) in &self.cells {
            for (normal, corners) in FACES {
                if self
                    .cells
                    .contains_key(&(x + normal[0], y + normal[1], z + normal[2]))
                {
                    continue;
                }
                let n = transform(normal.map(|v| v as f32));
                if n[2] <= 0.0 {
                    continue;
                }
                let light = -0.45 * n[0] + 0.65 * n[1] + 0.45 * n[2];
                let color = match material {
                    1 if light < 0.15 => 11,
                    1 if light > 0.65 => 3,
                    4 if light < 0.15 => 14,
                    4 if light > 0.65 => 2,
                    11 if light > 0.65 => 1,
                    15 if light < 0.15 => 13,
                    _ => material,
                };
                let vertices = corners
                    .map(|[dx, dy, dz]| project([x as f32 + dx, y as f32 + dy, z as f32 + dz]));
                for indices in [[0, 1, 2], [0, 2, 3]] {
                    raster_triangle(&mut sprite, &mut depth, indices.map(|i| vertices[i]), color);
                }
            }
        }
        sprite
    }
}

fn raster_triangle(sprite: &mut Sprite, depth: &mut [f32], v: [[f32; 3]; 3], color: u8) {
    let edge = |a: [f32; 3], b: [f32; 3], x: f32, y: f32| {
        (b[0] - a[0]) * (y - a[1]) - (b[1] - a[1]) * (x - a[0])
    };
    let area = edge(v[0], v[1], v[2][0], v[2][1]);
    if area.abs() < 0.00001 {
        return;
    }
    let min_x = v
        .iter()
        .map(|p| p[0])
        .fold(f32::INFINITY, f32::min)
        .floor()
        .max(0.0) as i32;
    let max_x = v
        .iter()
        .map(|p| p[0])
        .fold(f32::NEG_INFINITY, f32::max)
        .ceil()
        .min(f32::from(sprite.width) - 1.0) as i32;
    let min_y = v
        .iter()
        .map(|p| p[1])
        .fold(f32::INFINITY, f32::min)
        .floor()
        .max(0.0) as i32;
    let max_y = v
        .iter()
        .map(|p| p[1])
        .fold(f32::NEG_INFINITY, f32::max)
        .ceil()
        .min(f32::from(sprite.height) - 1.0) as i32;
    for y in min_y..=max_y {
        for x in min_x..=max_x {
            let (px, py) = (x as f32 + 0.5, y as f32 + 0.5);
            let a = edge(v[1], v[2], px, py) / area;
            let b = edge(v[2], v[0], px, py) / area;
            let c = 1.0 - a - b;
            if a < -0.0001 || b < -0.0001 || c < -0.0001 {
                continue;
            }
            let z = a * v[0][2] + b * v[1][2] + c * v[2][2];
            let i = y as usize * usize::from(sprite.width) + x as usize;
            if z > depth[i] {
                depth[i] = z;
                sprite.pixels[i] = Some(color);
            }
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

#[cfg(test)]
mod voxel_tests {
    use super::*;
    use crate::game::Stage;
    const STAGES: [Stage; 5] = [
        Stage::Egg,
        Stage::Baby,
        Stage::Child,
        Stage::Adult,
        Stage::Dead,
    ];
    const STYLES: [SpriteStyle; 2] = [SpriteStyle::EightBit, SpriteStyle::SixteenBit];
    const MOODS: [Expression; 16] = [
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
    fn voxel_all_stages_moods_and_rotations_are_bounded_indexed_and_transparent() {
        for style in STYLES {
            for stage in STAGES {
                for mood in MOODS {
                    for yaw in [0, 45, 90, 135, 180, 225, 270, 315] {
                        let s = Sprite::voxel(stage, mood, style, usize::MAX, yaw, true);
                        assert!(s.width <= 32 && s.height <= 32);
                        assert!(
                            s.pixels.iter().flatten().count() > 10,
                            "{stage:?} {mood:?} {yaw}"
                        );
                        assert!(s.pixels.contains(&None));
                        assert!(s.pixels.iter().flatten().all(|&c| c < 16));
                        // No pose or rotating prop touches a canvas edge.
                        for x in 0..u32::from(s.width) {
                            assert_eq!(s.pixel(x, 0, 1), None);
                            assert_eq!(s.pixel(x, u32::from(s.height) - 1, 1), None);
                        }
                        for y in 0..u32::from(s.height) {
                            assert_eq!(s.pixel(0, y, 1), None);
                            assert_eq!(s.pixel(u32::from(s.width) - 1, y, 1), None);
                        }
                        assert!(Sprite::new(s.width, s.height, s.pixels).is_ok());
                    }
                }
            }
        }
    }

    #[test]
    fn voxel_models_have_solid_depth_and_independent_style_geometry() {
        for style in STYLES {
            for stage in STAGES {
                let m = VoxelModel::pet(stage, Expression::Idle, style, 0, false);
                for z in -2..=2 {
                    assert!(m.cells.contains_key(&(0, 0, z)));
                }
                assert!(m.cells.len() > 100);
                assert!(m
                    .cells
                    .keys()
                    .all(|&(x, y, z)| x.abs() <= 12 && y.abs() <= 12 && z.abs() <= 12));
            }
        }
        let coarse = VoxelModel::pet(Stage::Adult, Expression::Idle, STYLES[0], 0, false);
        let fine = VoxelModel::pet(Stage::Adult, Expression::Idle, STYLES[1], 0, false);
        assert_ne!(coarse.cells, fine.cells);
        assert!(fine.cells.len() > coarse.cells.len());
        let small = Sprite::voxel(Stage::Adult, Expression::Idle, STYLES[0], 0, 45, false);
        let detail = Sprite::voxel(Stage::Adult, Expression::Idle, STYLES[1], 0, 45, false);
        assert_ne!(
            small.pixels,
            detail.downsample_to_fit(small.width, small.height).pixels
        );
    }

    #[test]
    fn voxel_front_side_back_and_growth_differ_and_yaw_wraps() {
        for style in STYLES {
            let mut stages = Vec::new();
            for stage in STAGES {
                let front = Sprite::voxel(stage, Expression::Idle, style, 0, 0, false);
                let side = Sprite::voxel(stage, Expression::Idle, style, 0, 90, false);
                let back = Sprite::voxel(stage, Expression::Idle, style, 0, 180, false);
                assert_ne!(front.pixels, side.pixels, "{stage:?}");
                assert_ne!(front.pixels, back.pixels, "{stage:?}");
                assert_ne!(side.pixels, back.pixels, "{stage:?}");
                for prior in &stages {
                    assert_ne!(&front.pixels, prior);
                }
                stages.push(front.pixels);
                for yaw in [0, 27, 90, 179, 359] {
                    assert_eq!(
                        Sprite::voxel(stage, Expression::Idle, style, usize::MAX, yaw, true).pixels,
                        Sprite::voxel(stage, Expression::Idle, style, 3, yaw + 360, true).pixels,
                    );
                }
                assert_eq!(
                    Sprite::voxel(stage, Expression::Happy, style, 0, u16::MAX, false).pixels,
                    Sprite::voxel(stage, Expression::Happy, style, 0, u16::MAX % 360, false).pixels,
                );
            }
        }
    }

    #[test]
    fn voxel_gait_is_three_dimensional_and_egg_dead_sleep_do_not_walk() {
        for style in STYLES {
            for stage in [Stage::Baby, Stage::Child, Stage::Adult] {
                let frames: Vec<_> = (0..4)
                    .map(|f| Sprite::voxel(stage, Expression::Idle, style, f, 45, true).pixels)
                    .collect();
                assert_ne!(frames[0], frames[1]);
                assert_ne!(frames[1], frames[3]);
                let a = VoxelModel::pet(stage, Expression::Idle, style, 0, true);
                let b = VoxelModel::pet(stage, Expression::Idle, style, 1, true);
                assert_ne!(a.cells, b.cells);
            }
            for stage in [Stage::Egg, Stage::Dead] {
                for frame in 0..4 {
                    assert_eq!(
                        Sprite::voxel(stage, Expression::Idle, style, frame, 90, true).pixels,
                        Sprite::voxel(stage, Expression::Idle, style, frame, 90, false).pixels,
                    );
                }
            }
            for frame in 0..4 {
                assert_eq!(
                    Sprite::voxel(Stage::Adult, Expression::Sleeping, style, frame, 0, true).pixels,
                    Sprite::voxel(Stage::Adult, Expression::Sleeping, style, frame, 0, false)
                        .pixels,
                );
            }
            for mood in MOODS {
                assert_eq!(
                    Sprite::voxel(Stage::Dead, mood, style, 0, 45, true).pixels,
                    Sprite::voxel(Stage::Adult, Expression::Dead, style, 0, 45, false).pixels,
                );
            }
        }
    }

    #[test]
    fn voxel_care_props_and_faces_change_both_art_and_animation() {
        for style in STYLES {
            for (i, mood) in MOODS.iter().enumerate() {
                let s = Sprite::voxel(Stage::Adult, *mood, style, 0, 0, false);
                for other in &MOODS[i + 1..] {
                    assert_ne!(
                        s.pixels,
                        Sprite::voxel(Stage::Adult, *other, style, 0, 0, false).pixels,
                        "{mood:?}/{other:?}"
                    );
                }
            }
            for mood in [
                Expression::Sleeping,
                Expression::Eating,
                Expression::Cleaning,
                Expression::Celebrate,
            ] {
                assert_ne!(
                    Sprite::voxel(Stage::Adult, mood, style, 0, 45, false).pixels,
                    Sprite::voxel(Stage::Adult, mood, style, 3, 45, false).pixels,
                    "{style:?} {mood:?}",
                );
            }
        }
    }

    #[test]
    fn voxel_depth_buffer_hides_the_far_surface_independent_of_draw_order() {
        let make = || Sprite::new(4, 4, vec![None; 16]).unwrap();
        let far = [[0.0, 0.0, -3.0], [4.0, 0.0, -3.0], [0.0, 4.0, -3.0]];
        let near = far.map(|[x, y, _]| [x, y, 3.0]);
        for reverse in [false, true] {
            let mut s = make();
            let mut z = vec![f32::NEG_INFINITY; 16];
            let surfaces = if reverse {
                [(near, 5), (far, 8)]
            } else {
                [(far, 8), (near, 5)]
            };
            for (triangle, color) in surfaces {
                raster_triangle(&mut s, &mut z, triangle, color);
            }
            assert_eq!(s.pixel(0, 0, 1), Some(5));
            assert_eq!(s.pixel(3, 3, 1), None);
        }
        let mut model = VoxelModel::default();
        model.ellipsoid([0.0; 3], [3.0; 3], 1);
        let back = model.rasterize(SpriteStyle::SixteenBit, 180, false);
        model.cells.insert((0, 0, 4), 12);
        assert_ne!(
            model.rasterize(SpriteStyle::SixteenBit, 0, false).pixels,
            back.pixels
        );
        assert_eq!(
            model.rasterize(SpriteStyle::SixteenBit, 180, false).pixels,
            back.pixels
        );
    }

    #[test]
    fn downsample_preserves_ratio_centers_transparency_and_never_enlarges() {
        let pixels: Vec<_> = (0..32)
            .map(|n| if n % 3 == 0 { None } else { Some(n % 16) })
            .collect();
        let s = Sprite::new(8, 4, pixels.clone()).unwrap();
        assert_eq!(s.downsample_to_fit(99, 99).pixels, pixels);
        let half = s.downsample_to_fit(4, 4);
        assert_eq!((half.width, half.height), (4, 2));
        for y in 0..2 {
            for x in 0..4 {
                assert_eq!(half.pixel(x, y, 1), s.pixel(x * 2 + 1, y * 2 + 1, 1));
            }
        }
        for (w, h) in [(1, 1), (1, 20), (20, 1), (3, 2), (u16::MAX, 1)] {
            let small = s.downsample_to_fit(w, h);
            assert!(small.width <= w && small.height <= h);
            assert!(small.width <= s.width && small.height <= s.height);
            assert!((i32::from(small.width) * 4 - i32::from(small.height) * 8).abs() <= 8);
            assert!(Sprite::new(small.width, small.height, small.pixels).is_ok());
        }
        for (w, h) in [(0, 0), (0, 5), (5, 0)] {
            let empty = s.downsample_to_fit(w, h);
            assert_eq!((empty.width, empty.height), (1, 1));
            assert_eq!(empty.pixels, vec![None]);
        }
        let tiny = Sprite::new(1, 1, vec![Some(15)]).unwrap();
        assert_eq!(
            tiny.downsample_to_fit(u16::MAX, u16::MAX).pixels,
            vec![Some(15)]
        );
        for style in STYLES {
            for stage in STAGES {
                let s = Sprite::voxel(stage, Expression::Idle, style, 0, 45, false);
                for (w, h) in [(1, 1), (2, 1), (1, 2), (7, 3)] {
                    let small = s.downsample_to_fit(w, h);
                    assert!(small.width <= w && small.height <= h);
                }
            }
        }
    }
}
