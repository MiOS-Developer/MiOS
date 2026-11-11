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

impl Expression {
    /// Printable ASCII, with a pure four-pose blink/chew cycle.
    pub fn kaomoji(self, frame: usize) -> &'static str {
        use Expression::*;
        match self {
            Idle if frame % 4 == 2 => "(-_-)",
            Idle => "(o_o)",
            Happy => "(^_^)",
            Sad => "(T_T)",
            Angry => "(>_<)",
            Surprised => "(O_O)",
            Sleeping => "(-_-)",
            Sick => "(+_+)",
            Hungry => "(u_u)",
            Playful if frame % 4 == 2 => "(>_o)",
            Playful => "(o_<)",
            Shy => "(._.)",
            Excited => "(*_*)",
            Eating if frame % 2 == 0 => "(o_O)",
            Eating => "(o_o)",
            Cleaning => "(o.o)",
            Healing => "(u_u)",
            Celebrate => "(^o^)",
            Dead => "(x_x)",
        }
    }
}

/// Face center and row in unscaled sprite pixels. Consumers apply the same
/// scale and terminal packing as the image. Absence means no visible face.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct FaceOverlay {
    pub center_x: u16,
    pub y: u16,
    pub text: &'static str,
}

/// Original animal geometry and indexed colors supplied by the character roster.
/// Untrusted indices are reduced to the sixteen supported shapes/colors.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Appearance {
    pub shape: u8,
    pub body: u8,
    pub accent: u8,
}

impl Default for Appearance {
    fn default() -> Self {
        Self {
            shape: 0,
            body: 1,
            accent: 11,
        }
    }
}

impl Appearance {
    fn bounded(self) -> Self {
        Self {
            shape: self.shape % 16,
            body: self.body % 16,
            accent: self.accent % 16,
        }
    }
}

#[derive(Clone, Debug)]
pub struct Sprite {
    width: u16,
    height: u16,
    pixels: Vec<Option<u8>>,
    face_overlay: Option<FaceOverlay>,
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
        Self::voxel_with_appearance(
            stage,
            expression,
            style,
            frame,
            yaw_degrees,
            walking,
            Appearance::default(),
        )
    }

    /// Render one of eight original animal families, with two volume variants
    /// each. Family is shape % 8; shapes 8..15 change geometry as well as color.
    pub fn voxel_with_appearance(
        stage: crate::game::Stage,
        expression: Expression,
        style: SpriteStyle,
        frame: usize,
        yaw_degrees: u16,
        walking: bool,
        appearance: Appearance,
    ) -> Self {
        use crate::game::Stage;
        let dead = stage == Stage::Dead || expression == Expression::Dead;
        let stage = if dead { Stage::Dead } else { stage };
        let expression = if dead { Expression::Dead } else { expression };
        let mut model = VoxelModel::pet(stage, expression, style, frame % 4, walking);
        model.animal(stage, style, appearance.bounded());
        model.grow_body(
            stage,
            expression,
            style,
            frame % 4,
            walking,
            appearance.bounded(),
        );
        model.attach_face(stage, expression, style, frame);
        model.rasterize(style, yaw_degrees, expression == Expression::Sleeping)
    }

    /// Palette-only fallback for the existing pixel artwork. Preserve eyes,
    /// blush, mouth and props; body highlights use the selected body's ramp.
    pub fn recolored(&self, body: u8, accent: u8) -> Self {
        let (body, accent) = (body % 16, accent % 16);
        let mut sprite = self.clone();
        for color in sprite.pixels.iter_mut().flatten() {
            *color = match *color {
                1 => body,
                3 => voxel_shade(body, 1.0),
                11 => accent,
                other => other,
            };
        }
        sprite
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
                face_overlay: None,
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
        let face_overlay = self.face_overlay.map(|face| FaceOverlay {
            center_x: (u32::from(face.center_x) * u32::from(width) / sw).min(u32::from(width) - 1)
                as u16,
            y: (u32::from(face.y) * u32::from(height) / sh).min(u32::from(height) - 1) as u16,
            text: face.text,
        });
        Self {
            width,
            height,
            pixels,
            face_overlay,
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
        let size = rows[0].len() as u16;
        let grown = matches!(stage, Stage::Child | Stage::Adult);
        let mut sprite = Self {
            width: size,
            height: if grown { size * 2 } else { size },
            face_overlay: None,
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
        sprite
            .pixels
            .resize(usize::from(sprite.width) * usize::from(sprite.height), None);
        let phase = frame % 4;
        match style {
            SpriteStyle::EightBit => sprite.animate_small(stage, expression, phase),
            SpriteStyle::SixteenBit => sprite.animate_detailed(stage, expression, phase),
        }
        if grown {
            sprite.pixel_body(stage, expression, style, phase);
        }
        if stage != Stage::Egg {
            let y = match (style, stage) {
                (SpriteStyle::EightBit, Stage::Baby | Stage::Child) => 4,
                (SpriteStyle::EightBit, _) => 3,
                (SpriteStyle::SixteenBit, Stage::Baby) => 8,
                (SpriteStyle::SixteenBit, Stage::Child) => 7,
                (SpriteStyle::SixteenBit, _) => 6,
            };
            sprite.face_overlay = Some(FaceOverlay {
                center_x: size / 2,
                y,
                text: expression.kaomoji(frame),
            });
        }
        sprite
    }

    /// Independent neck, torso, arms and legs beneath the original pixel head.
    fn pixel_body(
        &mut self,
        stage: crate::game::Stage,
        expression: Expression,
        style: SpriteStyle,
        phase: usize,
    ) {
        use Expression::*;
        let fine = style == SpriteStyle::SixteenBit;
        let top = if fine { 14 } else { 7 };
        for y in top..i32::from(self.height) {
            for x in 0..i32::from(self.width) {
                self.pixels[y as usize * usize::from(self.width) + x as usize] = None;
            }
        }
        let adult = stage == crate::game::Stage::Adult;
        let gait: i32 = if matches!(expression, Sleeping | Sick) {
            0
        } else {
            [0, 1, 0, -1][phase]
        };
        let raised = matches!(expression, Happy | Playful | Excited | Celebrate);
        if fine {
            self.stamp(6, 14, &["0110", "0330"]);
            for y in 16..if adult { 25 } else { 23 } {
                self.stamp(4, y, &[if y == 16 { "00333300" } else { "01344310" }]);
            }
            let arm_y = if raised { 13 + (phase % 2) as i32 } else { 18 };
            self.stamp(1, arm_y, &["030", "010", "0a0", ".0."]);
            self.stamp(12, arm_y, &["030", "010", "0a0", ".0."]);
            if matches!(expression, Eating | Shy) {
                self.stamp(5, 12, &["03300330", ".aa..aa."]);
            }
            let hips = if adult { 25 } else { 23 };
            self.stamp(4, hips, &["0aaaaaa0", ".00..00."]);
            let foot_drop = if adult { 4 } else { 5 };
            for (x, lift) in [(4, gait.max(0)), (10, (-gait).max(0))] {
                for y in hips + 2..hips + foot_drop + 1 - lift {
                    self.stamp(x, y, &["010"]);
                }
                self.stamp(x - 1, hips + foot_drop - lift, &["0ee0", "0000"]);
            }
            self.paint(5, 19, 15);
            self.paint(10, 21, 10);
        } else {
            self.stamp(3, 7, &["00", "33"]);
            for y in 9..if adult { 13 } else { 12 } {
                self.stamp(2, y, &["0140"]);
            }
            let arm_y = if raised { 7 + (phase % 2) as i32 } else { 10 };
            self.stamp(0, arm_y, &["01", "0a"]);
            self.stamp(6, arm_y, &["10", "a0"]);
            if matches!(expression, Eating | Shy) {
                self.stamp(2, 6, &["1aa1"]);
            }
            let hips = if adult { 13 } else { 12 };
            self.stamp(2, hips, &["0aa0"]);
            self.paint(2, hips + 1 - gait.max(0), 1);
            self.paint(5, hips + 1 - (-gait).max(0), 1);
            self.stamp(1, hips + 2 - gait.max(0), &["0e"]);
            self.stamp(5, hips + 2 - (-gait).max(0), &["e0"]);
        }
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

    pub fn face_overlay(&self) -> Option<FaceOverlay> {
        self.face_overlay
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
            face_overlay: None,
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
            face_overlay: Some(FaceOverlay {
                center_x: 4,
                y: 3,
                text: if stage == Stage::Dead {
                    Expression::Dead.kaomoji(frame)
                } else {
                    Expression::Idle.kaomoji(frame)
                },
            }),
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
            face_overlay: None,
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
            face_overlay: Some(FaceOverlay {
                center_x: 4,
                y: 3,
                text: Expression::Idle.kaomoji(frame),
            }),
        }
    }
}

// A sparse integer lattice contains entire solid ellipsoids, including their
// hidden interiors. Only exposed cube faces are projected. Front relief and the
// rear seed seam therefore disappear naturally behind the body when it turns.
#[derive(Default)]
struct VoxelModel {
    appearance: Option<Appearance>,
    tall: bool,
    face: Option<([f32; 3], &'static str)>,
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
                let gait: f32 = if walking && !matches!(expression, Sleeping | Sick) {
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
    /// Keep the animal's sculpted head at its original proportions, lift it,
    /// and grow an independently solid torso and articulated limbs below it.
    fn grow_body(
        &mut self,
        stage: crate::game::Stage,
        expression: Expression,
        style: SpriteStyle,
        phase: usize,
        walking: bool,
        appearance: Appearance,
    ) {
        use crate::game::Stage;
        use Expression::*;
        if !matches!(stage, Stage::Child | Stage::Adult) {
            return;
        }
        self.tall = true;
        let fine = style == SpriteStyle::SixteenBit;
        let adult = stage == Stage::Adult;
        let lift = match (fine, adult) {
            (false, false) => 6,
            (false, true) => 7,
            (true, false) => 9,
            (true, true) => 11,
        };
        let (rx, ry, rz): (f32, f32, f32) = match (fine, adult) {
            (false, false) => (4.0, 3.8, 3.0),
            (false, true) => (4.4, 4.5, 3.5),
            (true, false) => (5.5, 5.5, 4.0),
            (true, true) => (6.2, 6.5, 4.8),
        };
        self.cells = self
            .cells
            .iter()
            .filter_map(|(&(x, y, z), &color)| {
                // Remove the old head's stub feet and lower hem, then relocate its
                // tail to the new hips instead of leaving a tail behind the neck.
                if color == 14 || (y as f32) < -ry * 0.82 {
                    return None;
                }
                let tail = (z as f32) < -rz && y < 0;
                Some(((x, y + if tail { 0 } else { lift }, z), color))
            })
            .collect();
        let family = appearance.shape % 8;
        let variant = appearance.shape >= 8;
        let waist = rx
            * match family {
                1 => 0.56,
                2 | 4 => 0.78,
                5 => 0.86,
                7 => 0.72,
                _ => 0.65,
            };
        let torso_y = if fine { -2.0 } else { -1.0 };
        let torso_h = match (fine, adult) {
            (false, false) => 4.8,
            (false, true) => 6.0,
            (true, false) => 7.0,
            (true, true) => 9.0,
        };
        let torso_z = rz * 0.80;
        self.ellipsoid(
            [0.0, torso_y + torso_h * 0.7, 0.0],
            [waist * 0.62, torso_h * 0.55, torso_z * 0.65],
            16,
        );
        self.ellipsoid([0.0, torso_y, 0.0], [waist, torso_h, torso_z], 16);
        self.ellipsoid(
            [0.0, torso_y - 0.6, torso_z * 0.8],
            [waist * 0.72, torso_h * 0.65, torso_z * 0.4],
            4,
        );
        if family == 7 {
            self.ellipsoid(
                [0.0, torso_y, -torso_z * 0.7],
                [waist + 0.5, torso_h * 0.9, torso_z],
                17,
            );
            for x in [-1.0, 1.0] {
                self.ellipsoid(
                    [x * waist * 0.5, torso_y, -torso_z * 1.55],
                    [0.7, torso_h * 0.65, 0.8],
                    16,
                );
            }
        }
        let gait = if walking && !matches!(expression, Sleeping | Sick | Dead) {
            [0.0, 1.0, 0.0, -1.0][phase]
        } else {
            0.0
        };
        let raised = matches!(expression, Happy | Playful | Excited | Celebrate);
        let arm_h = if fine { 3.0 } else { 2.1 };
        for sign in [-1.0_f32, 1.0] {
            let forward = if matches!(expression, Eating | Shy) {
                torso_z + 1.0
            } else {
                -gait * sign * 1.4
            };
            let hand_y = if raised {
                torso_y + torso_h * 0.85 + (phase % 2) as f32
            } else {
                torso_y - torso_h * 0.2
            };
            let side = if expression == Shy {
                waist * 0.6
            } else {
                waist + 0.8
            };
            self.ellipsoid(
                [sign * side, hand_y + arm_h * 0.45, forward * 0.5],
                [if family == 6 { 1.6 } else { 1.1 }, arm_h, 1.3],
                if family == 6 { 17 } else { 16 },
            );
            self.ellipsoid(
                [sign * side, hand_y - arm_h * 0.45, forward],
                [1.2, 1.2, 1.4],
                16,
            );
            let hips = torso_y - torso_h * 0.7;
            let leg_h = if fine { 3.8 } else { 2.6 };
            let knee_y = hips - leg_h + (gait * sign).max(0.0);
            let leg_z = gait * sign * if fine { 2.2 } else { 1.6 };
            self.ellipsoid(
                [sign * waist * 0.55, hips - leg_h * 0.3, leg_z * 0.4],
                [if fine { 1.5 } else { 1.0 }, leg_h, 1.2],
                if variant { 17 } else { 16 },
            );
            self.ellipsoid(
                [sign * waist * 0.55, knee_y - leg_h * 0.55, 0.7 + leg_z],
                [
                    if family == 5 { 2.0 } else { 1.5 },
                    1.0,
                    if fine { 2.2 } else { 1.6 },
                ],
                14,
            );
        }
        if variant {
            self.ellipsoid(
                [0.0, torso_y + torso_h * 0.6, torso_z],
                [waist * 0.8, 0.8, 0.8],
                17,
            );
        }
    }

    fn attach_face(
        &mut self,
        stage: crate::game::Stage,
        expression: Expression,
        style: SpriteStyle,
        frame: usize,
    ) {
        use crate::game::Stage;
        if stage == Stage::Egg {
            return;
        }
        let fine = style == SpriteStyle::SixteenBit;
        let ry: f32 = match (fine, stage) {
            (false, Stage::Baby) => 2.8,
            (false, Stage::Child) => 3.8,
            (false, _) => 4.5,
            (true, Stage::Baby) => 4.0,
            (true, Stage::Child) => 5.5,
            (true, _) => 6.5,
        };
        let lift = match (fine, stage) {
            (false, Stage::Child) => 6,
            (false, Stage::Adult) => 7,
            (true, Stage::Child) => 9,
            (true, Stage::Adult) => 11,
            _ => 0,
        };
        let y = (ry * 0.24) as i32 + lift;
        if let Some(z) = self
            .cells
            .keys()
            .filter(|&&(x, cy, _)| x == 0 && cy == y)
            .map(|&(_, _, z)| z)
            .max()
        {
            self.face = Some(([0.0, y as f32, z as f32 + 0.5], expression.kaomoji(frame)));
        }
    }

    fn rasterize(&self, style: SpriteStyle, yaw: u16, sleeping: bool) -> Sprite {
        let size: u16 = if style == SpriteStyle::EightBit {
            24
        } else {
            32
        };
        let mut sprite = Sprite {
            width: size,
            height: if self.tall { size * 2 } else { size },
            pixels: vec![
                None;
                usize::from(size) * usize::from(size) * if self.tall { 2 } else { 1 }
            ],
            face_overlay: None,
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
        // Uniform projection fitting includes all cube corners and care props.
        // This retains volume, pitch and aspect while leaving a clear border.
        let mut bounds = [
            f32::INFINITY,
            f32::NEG_INFINITY,
            f32::INFINITY,
            f32::NEG_INFINITY,
        ];
        for &(x, y, z) in self.cells.keys() {
            for dx in [-0.5, 0.5] {
                for dy in [-0.5, 0.5] {
                    for dz in [-0.5, 0.5] {
                        let [px, py, _] = transform([x as f32 + dx, y as f32 + dy, z as f32 + dz]);
                        bounds[0] = bounds[0].min(px);
                        bounds[1] = bounds[1].max(px);
                        bounds[2] = bounds[2].min(py);
                        bounds[3] = bounds[3].max(py);
                    }
                }
            }
        }
        if self.cells.is_empty() {
            return sprite;
        }
        let height = sprite.height;
        let origin_y = f32::from(height) * if self.tall { 0.52 } else { 0.57 };
        // Keep the origin fixed while limbs animate. Only a pose extending
        // beyond the usable canvas needs uniform reduction around that origin.
        let fit = ((f32::from(size) * 0.5 - 2.0) / bounds[0].abs().max(bounds[1].abs()).max(1.0))
            .min((origin_y - 2.0) / bounds[3].max(1.0))
            .min((f32::from(height) - origin_y - 2.0) / (-bounds[2]).max(1.0))
            .min(1.0);
        let project = |v: [f32; 3]| {
            let [x, y, z] = transform(v);
            // Align lattice centers with pixel centers at zero yaw. Otherwise
            // an adjacent raised eye can win depth at the head-center anchor.
            [f32::from(size) * 0.5 + 0.5 + x * fit, origin_y - y * fit, z]
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
                    16 => voxel_shade(self.appearance.unwrap_or_default().body, light),
                    17 => voxel_shade(self.appearance.unwrap_or_default().accent, light),
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
        if c * cp > 0.72 {
            if let Some((anchor, text)) = self.face {
                let [x, y, z] = project(anchor);
                let (x, y) = (x.floor() as i32, y.floor() as i32);
                if x >= 0 && y >= 0 && x < i32::from(size) && y < i32::from(height) {
                    let i = y as usize * usize::from(size) + x as usize;
                    if sprite.pixels[i].is_some() && (depth[i] - z).abs() <= 1.25 {
                        sprite.face_overlay = Some(FaceOverlay {
                            center_x: x as u16,
                            y: y as u16,
                            text,
                        });
                    }
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

impl VoxelModel {
    fn animal(&mut self, stage: crate::game::Stage, style: SpriteStyle, appearance: Appearance) {
        use crate::game::Stage;
        let fine = style == SpriteStyle::SixteenBit;
        let (rx, ry, rz) = match (fine, stage) {
            (false, Stage::Egg) => (4.0, 5.0, 3.0),
            (false, Stage::Baby) => (3.0, 2.8, 2.5),
            (false, Stage::Child) => (4.0, 3.8, 3.0),
            (false, _) => (4.4, 4.5, 3.5),
            (true, Stage::Egg) => (5.5, 7.0, 4.5),
            (true, Stage::Baby) => (4.5, 4.0, 3.5),
            (true, Stage::Child) => (5.5, 5.5, 4.0),
            (true, _) => (6.2, 6.5, 4.8),
        };
        let variant = appearance.shape >= 8;
        // Retain the existing emotion's highest relief sample per face column,
        // then move it onto the new muzzle after adding the animal volumes.
        let eye_y = if stage == Stage::Egg {
            0
        } else {
            (ry * 0.24) as i32
        };
        let mut face = std::collections::BTreeMap::new();
        for (&(x, y, z), &color) in &self.cells {
            if x.abs() <= if fine { 4 } else { 3 }
                && (eye_y - 4..=eye_y + 3).contains(&y)
                && z > 0
                && matches!(color, 0 | 2 | 5 | 6)
            {
                let old = face.entry((x, y)).or_insert((z, color));
                if z > old.0 {
                    *old = (z, color);
                }
            }
        }
        if !matches!(stage, Stage::Egg | Stage::Dead) {
            // Remove the generic sprout above the rounded crown; families grow
            // their own ears/crest. Keep all care props below this crown.
            self.cells.retain(|&(_, y, _), _| y as f32 <= ry + 0.5);
        }
        for color in self.cells.values_mut() {
            *color = match *color {
                1 => 16,
                11 => 17,
                4 if stage == Stage::Egg => 16,
                15 if stage == Stage::Dead => 16,
                other => other,
            };
        }
        self.appearance = Some(appearance);
        match appearance.shape % 8 {
            0 => {
                // Neko: tapering cat ears, paired muzzle and a curled tail.
                for sign in [-1.0, 1.0] {
                    for level in 0..3 {
                        let h = level as f32;
                        let folded = variant && sign < 0.0;
                        self.ellipsoid(
                            [
                                sign * (rx * 0.65 + if folded { h * 0.35 } else { 0.0 }),
                                ry * 0.82 + h * if folded { 0.45 } else { 0.9 },
                                -0.3,
                            ],
                            [1.5 - h * 0.4, 1.0, 1.1 - h * 0.2],
                            16,
                        );
                    }
                    self.ellipsoid([sign * 1.0, -0.8, rz], [1.2, 0.9, 1.0], 17);
                }
                for step in 0..4 {
                    let t = step as f32;
                    self.ellipsoid(
                        [
                            rx * 0.7 + t * 0.5,
                            -ry * 0.65 + if variant { t * 1.1 } else { t * 0.35 },
                            -rz - 0.6,
                        ],
                        [0.8, 0.9, 0.9],
                        if step == 3 { 17 } else { 16 },
                    );
                }
            }
            1 => {
                // Kitsune: tall narrow ears, a pointed snout and a bushy tail.
                for sign in [-1.0, 1.0] {
                    self.ellipsoid([sign * rx * 0.55, ry + 0.4, -0.7], [1.1, 2.6, 1.1], 16);
                    self.ellipsoid([sign * rx * 0.55, ry + 0.5, 0.0], [0.6, 1.6, 0.6], 17);
                }
                self.ellipsoid([0.0, -0.8, rz + 0.6], [1.9, 1.2, 1.8], 17);
                self.ellipsoid([0.0, -0.8, rz + 2.0], [0.8, 0.7, 0.8], 0);
                let tail_y = if variant { 1.0 } else { -ry * 0.4 };
                self.ellipsoid(
                    [rx * 0.5, tail_y, -rz - 1.6],
                    [2.0, if variant { 3.0 } else { 1.6 }, 2.2],
                    16,
                );
                self.ellipsoid(
                    [
                        rx * 0.5,
                        tail_y + if variant { 2.0 } else { 0.0 },
                        -rz - 2.6,
                    ],
                    [1.4, 1.2, 1.3],
                    17,
                );
                if variant {
                    self.ellipsoid([0.0, ry + 0.5, 1.0], [1.0, 1.6, 0.8], 17);
                }
            }
            2 => {
                // Tanuki: small round ears, cheek mask and a ringed barrel tail.
                for sign in [-1.0, 1.0] {
                    self.ellipsoid([sign * rx * 0.65, ry * 0.8, -0.6], [1.6, 1.6, 1.3], 17);
                    self.ellipsoid([sign * 1.8, eye_y as f32, rz * 0.65], [1.7, 1.3, 1.1], 17);
                }
                self.ellipsoid([0.0, -1.0, rz], [1.7, 1.0, 1.2], 16);
                self.ellipsoid([rx * 0.6, -ry * 0.5, -rz - 1.5], [1.7, 1.7, 2.3], 17);
                self.ellipsoid([rx * 0.6, -ry * 0.5, -rz - 2.8], [1.8, 1.7, 0.6], 16);
                if variant {
                    // Original little leaf cap, with a solid stem.
                    self.ellipsoid([0.0, ry + 0.4, 0.0], [2.4, 0.8, 1.8], 17);
                    self.ellipsoid([0.5, ry + 1.4, 0.0], [0.7, 1.0, 0.7], 16);
                }
            }
            3 => {
                // Usagi: long upright ears or one floppy ear, plus a puff tail.
                for sign in [-1.0, 1.0] {
                    let flop = variant && sign > 0.0;
                    let center = [
                        sign * if flop { rx * 0.7 + 1.0 } else { rx * 0.45 },
                        ry + if flop { 0.8 } else { 2.0 },
                        0.0,
                    ];
                    self.ellipsoid(
                        center,
                        [
                            if flop { 2.6 } else { 1.0 },
                            if flop { 1.0 } else { 3.0 },
                            1.0,
                        ],
                        16,
                    );
                    self.ellipsoid(
                        [center[0], center[1], 0.6],
                        [
                            if flop { 1.8 } else { 0.5 },
                            if flop { 0.5 } else { 2.0 },
                            0.6,
                        ],
                        17,
                    );
                }
                self.ellipsoid([0.0, -ry * 0.6, -rz - 1.0], [1.8, 1.8, 1.8], 17);
                if variant {
                    self.ellipsoid([-rx, 0.0, 0.0], [1.7, 2.0, 1.2], 17);
                }
            }
            4 => {
                // Kuma: round ears, broad muzzle and a little round rear tail.
                for sign in [-1.0, 1.0] {
                    self.ellipsoid([sign * rx * 0.7, ry * 0.8, -0.3], [2.0, 2.0, 1.7], 16);
                    self.ellipsoid([sign * rx * 0.7, ry * 0.8, 1.0], [1.1, 1.1, 0.7], 17);
                }
                self.ellipsoid([0.0, -1.0, rz], [2.5, 1.7, 1.4], 17);
                self.ellipsoid([0.0, -ry * 0.6, -rz - 0.8], [1.3, 1.3, 1.3], 16);
                if variant {
                    self.ellipsoid([0.0, ry + 0.8, 0.0], [1.0, 1.7, 1.0], 17);
                    for sign in [-1.0, 1.0] {
                        self.ellipsoid([sign * 1.6, -ry * 0.5, rz], [1.5, 1.0, 1.0], 17);
                    }
                }
            }
            5 => {
                // Kaeru: broad haunches and rounded brow lobes. The second
                // original pond creature grows a kappa-like dish and duck bill.
                self.ellipsoid([0.0, -1.0, 0.0], [rx + 0.8, ry * 0.7, rz], 16);
                for sign in [-1.0, 1.0] {
                    self.ellipsoid([sign * rx * 0.6, ry * 0.55, 0.3], [1.8, 2.0, 1.7], 16);
                    self.ellipsoid([sign * rx, -ry * 0.7, -0.4], [1.8, 1.2, 1.6], 17);
                }
                if variant {
                    for x in -2..=2 {
                        for z in -2..=2 {
                            if (3..=6).contains(&(x * x + z * z)) {
                                self.cells.insert((x, ry as i32 + 1, z), 17);
                            }
                        }
                    }
                    self.ellipsoid([0.0, -0.5, rz + 0.6], [2.7, 0.8, 1.7], 17);
                } else {
                    self.ellipsoid([0.0, -1.5, rz + 0.4], [2.8, 0.8, 0.9], 17);
                }
            }
            6 => {
                // Garden bird: thick wings, a beak, a tail fan and a crest.
                for sign in [-1.0, 1.0] {
                    self.ellipsoid(
                        [sign * rx, -0.5, -0.5],
                        [1.5, if variant { 3.5 } else { 2.3 }, 2.1],
                        17,
                    );
                    self.ellipsoid([sign * 1.7, -ry * 0.2, -rz - 0.8], [1.2, 1.5, 2.0], 17);
                }
                self.ellipsoid(
                    [0.0, -0.6, rz + 0.4],
                    [
                        if variant { 0.8 } else { 1.6 },
                        0.9,
                        if variant { 2.3 } else { 1.3 },
                    ],
                    17,
                );
                self.ellipsoid(
                    [0.0, ry + 0.4, -0.3],
                    [1.0, if variant { 2.0 } else { 1.0 }, 1.4],
                    17,
                );
                if variant {
                    self.ellipsoid([1.0, ry + 1.6, -0.8], [0.8, 1.2, 1.0], 16);
                }
            }
            _ => {
                // Kame: domed rear shell, raised shell ribs and four flippers.
                self.ellipsoid([0.0, 0.0, -rz * 0.45], [rx + 0.7, ry * 0.85, rz + 0.6], 17);
                for sign in [-1.0, 1.0] {
                    for up in [-1.0, 1.0] {
                        self.ellipsoid([sign * rx, up * ry * 0.55, -0.7], [1.7, 1.0, 1.8], 16);
                    }
                    self.ellipsoid([sign * 2.0, 0.0, -rz - 1.2], [0.7, ry * 0.55, 1.0], 16);
                }
                self.ellipsoid([0.0, 0.0, rz * 0.8], [rx * 0.65, ry * 0.55, rz * 0.7], 16);
                if variant {
                    for x in [-2.0, 0.0, 2.0] {
                        self.ellipsoid([x, ry * 0.7, -rz * 0.6], [0.8, 2.0, 1.0], 16);
                    }
                } else {
                    self.ellipsoid([0.0, -ry * 0.5, -rz - 1.2], [1.1, 0.7, 1.8], 16);
                }
            }
        }
        for ((x, y), (_, color)) in face {
            self.dot(x, y, color);
        }
    }
}

// The SSOT palette's existing ramps provide light/dark faces without inventing
// colors or exceeding index 15. Unknown ramps retain their selected base color.
fn voxel_shade(color: u8, light: f32) -> u8 {
    let (dark, bright) = match color {
        1 => (11, 3),
        3 => (1, 15),
        4 => (14, 2),
        7 => (8, 4),
        8 => (14, 7),
        9 => (10, 13),
        11 => (0, 1),
        12 => (6, 5),
        13 => (9, 2),
        14 => (0, 4),
        15 => (11, 2),
        other => (other, other),
    };
    if light < 0.15 {
        dark
    } else if light > 0.65 {
        bright
    } else {
        color
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
mod body_face_tests {
    use super::*;
    use crate::game::Stage;
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
    fn ascii_faces_are_bounded_printable_periodic_and_state_specific() {
        for mood in MOODS {
            for frame in [0, 1, 2, 3, 4, usize::MAX] {
                let text = mood.kaomoji(frame);
                assert_eq!(text.len(), 5);
                assert!(text.bytes().all(|b| (32..=126).contains(&b)));
                assert_eq!(text, mood.kaomoji(frame % 4));
                for style in STYLES {
                    let pixel = Sprite::animated(Stage::Adult, mood, style, frame);
                    assert_eq!(pixel.face_overlay().unwrap().text, text);
                }
            }
        }
        assert_eq!(Expression::Dead.kaomoji(0), "(x_x)");
        assert_eq!(Expression::Sleeping.kaomoji(usize::MAX), "(-_-)");
        assert_ne!(Expression::Idle.kaomoji(0), Expression::Idle.kaomoji(2));
        for style in STYLES {
            for mood in MOODS {
                assert_eq!(
                    Sprite::animated(Stage::Dead, mood, style, 1)
                        .face_overlay()
                        .unwrap()
                        .text,
                    "(x_x)"
                );
            }
            assert!(Sprite::animated(Stage::Egg, Expression::Idle, style, 0)
                .face_overlay()
                .is_none());
        }
    }

    #[test]
    fn older_stages_have_double_height_and_separate_lower_anatomy() {
        for style in STYLES {
            let size = if style == SpriteStyle::EightBit {
                8
            } else {
                16
            };
            let baby = Sprite::animated(Stage::Baby, Expression::Idle, style, 0);
            assert_eq!((baby.width, baby.height), (size, size));
            for stage in [Stage::Child, Stage::Adult] {
                let pixel = Sprite::animated(stage, Expression::Idle, style, 0);
                assert_eq!((pixel.width, pixel.height), (size, size * 2));
                for y in [
                    size + 1,
                    size * 3 / 2,
                    size * 2 - if stage == Stage::Child { 3 } else { 2 },
                ] {
                    assert!(
                        (0..u32::from(size)).any(|x| pixel.pixel(x, u32::from(y), 1).is_some()),
                        "{style:?} {stage:?} lower row {y}"
                    );
                }
                // Clear air between two feet proves that the lower half is not
                // a stretched, continuously filled head silhouette.
                let feet_y = if style == SpriteStyle::EightBit {
                    if stage == Stage::Adult {
                        15
                    } else {
                        14
                    }
                } else if stage == Stage::Adult {
                    30
                } else {
                    28
                };
                assert!(pixel.pixel(u32::from(size / 2), feet_y, 1).is_none());
                let voxel = Sprite::voxel(stage, Expression::Idle, style, 0, 0, false);
                assert_eq!(
                    (voxel.width, voxel.height),
                    if style == SpriteStyle::EightBit {
                        (24, 48)
                    } else {
                        (32, 64)
                    }
                );
                assert!(
                    (voxel.height / 2..voxel.height - 2)
                        .filter(|&y| (0..voxel.width)
                            .any(|x| voxel.pixel(u32::from(x), u32::from(y), 1).is_some()))
                        .count()
                        > usize::from(size / 2)
                );
                assert!(voxel.face_overlay().unwrap().y < voxel.height / 2);
            }
        }
    }

    #[test]
    fn all_sixteen_bodies_have_solid_torso_and_two_lower_limbs() {
        for style in STYLES {
            for stage in [Stage::Child, Stage::Adult] {
                for shape in 0..16 {
                    let appearance = Appearance {
                        shape,
                        ..Appearance::default()
                    };
                    let mut model = VoxelModel::pet(stage, Expression::Idle, style, 0, false);
                    model.animal(stage, style, appearance);
                    model.grow_body(stage, Expression::Idle, style, 0, false, appearance);
                    for z in -1..=1 {
                        assert!(model.cells.contains_key(&(0, -2, z)));
                    }
                    let low = if style == SpriteStyle::EightBit {
                        -6
                    } else {
                        -10
                    };
                    for side in [-1, 1] {
                        assert!(
                            model
                                .cells
                                .keys()
                                .filter(|&&(x, y, _)| x * side > 0 && y < low)
                                .count()
                                > 8,
                            "{style:?} {stage:?} shape={shape} leg={side}"
                        );
                    }
                    assert!(model.tall);
                }
            }
        }
    }

    #[test]
    fn projected_head_faces_follow_growth_and_hide_at_side_rear_and_occluders() {
        for style in STYLES {
            for stage in [Stage::Baby, Stage::Child, Stage::Adult] {
                for shape in 0..16 {
                    let appearance = Appearance {
                        shape,
                        ..Appearance::default()
                    };
                    let front = Sprite::voxel_with_appearance(
                        stage,
                        Expression::Happy,
                        style,
                        usize::MAX,
                        0,
                        false,
                        appearance,
                    );
                    let anchor = front.face_overlay().expect("front-facing head");
                    assert_eq!(anchor.text, "(^_^)");
                    assert!(anchor.center_x < front.width && anchor.y < front.height);
                    for yaw in [90, 135, 180, 225, 270] {
                        assert!(Sprite::voxel_with_appearance(
                            stage,
                            Expression::Happy,
                            style,
                            usize::MAX,
                            yaw,
                            false,
                            appearance
                        )
                        .face_overlay()
                        .is_none());
                    }
                }
            }
        }
        let mut model = VoxelModel::default();
        model.ellipsoid([0.0; 3], [3.0; 3], 1);
        model.face = Some(([0.0, 0.0, 3.5], "(o_o)"));
        assert!(model
            .rasterize(SpriteStyle::SixteenBit, 0, false)
            .face_overlay()
            .is_some());
        model.ellipsoid([0.0, 0.0, 8.0], [3.0; 3], 8);
        assert!(model
            .rasterize(SpriteStyle::SixteenBit, 0, false)
            .face_overlay()
            .is_none());
    }

    #[test]
    fn recolor_and_rational_downsample_preserve_safe_face_metadata() {
        for style in STYLES {
            for pixel in [true, false] {
                let s = if pixel {
                    Sprite::animated(Stage::Adult, Expression::Sad, style, 0)
                } else {
                    Sprite::voxel(Stage::Adult, Expression::Sad, style, 0, 0, false)
                };
                let face = s.face_overlay().unwrap();
                assert_eq!(s.recolored(255, 254).face_overlay(), Some(face));
                for (w, h) in [(1, 1), (2, 3), (7, 5), (11, 30), (u16::MAX, u16::MAX)] {
                    let fit = s.downsample_to_fit(w, h);
                    let anchor = fit.face_overlay().unwrap();
                    assert!(anchor.center_x < fit.width && anchor.y < fit.height);
                    assert_eq!(
                        anchor.center_x,
                        (u32::from(face.center_x) * u32::from(fit.width) / u32::from(s.width))
                            as u16
                    );
                    assert_eq!(
                        anchor.y,
                        (u32::from(face.y) * u32::from(fit.height) / u32::from(s.height)) as u16
                    );
                    assert_eq!(anchor.text, "(T_T)");
                }
                assert!(s.downsample_to_fit(0, 5).face_overlay().is_none());
            }
        }
        assert!(Sprite::new(1, 1, vec![Some(1)])
            .unwrap()
            .face_overlay()
            .is_none());
    }

    #[test]
    fn extreme_phases_and_yaws_keep_both_geometry_and_metadata_periodic() {
        for style in STYLES {
            for mood in MOODS {
                for yaw in [0, 359, u16::MAX] {
                    let a = Sprite::voxel(Stage::Adult, mood, style, usize::MAX, yaw, true);
                    let b =
                        Sprite::voxel(Stage::Adult, mood, style, usize::MAX % 4, yaw % 360, true);
                    assert_eq!(a.pixels, b.pixels);
                    assert_eq!(a.face_overlay(), b.face_overlay());
                    assert!(a.pixels.iter().flatten().all(|&c| c < 16));
                    if let Some(face) = a.face_overlay() {
                        assert!(face.center_x < a.width && face.y < a.height);
                    }
                }
            }
        }
    }
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
                        let height = if matches!(stage, Stage::Child | Stage::Adult)
                            && expression != Expression::Dead
                        {
                            size * 2
                        } else {
                            size
                        };
                        assert_eq!((sprite.width(), sprite.height()), (size, height));
                        assert_eq!(sprite.pixels.len(), usize::from(size * height));
                        assert!(sprite.pixels.iter().flatten().count() >= 16);
                        assert!(sprite.pixels.contains(&None), "transparent surroundings");
                        assert!(sprite.pixels.iter().flatten().all(|&color| color < 16));
                        assert_eq!(sprite.pixel(u32::MAX, u32::MAX, u16::MAX), None);
                        assert_eq!(sprite.pixel(0, 0, 0), None);
                        assert_eq!(sprite.pixel(u32::from(size), 0, 1), None);
                        assert_eq!(sprite.pixel(0, u32::from(height), 1), None);
                        assert_eq!(
                            sprite.pixels,
                            Sprite::animated(stage, expression, style, frame % 4).pixels
                        );
                        // Revalidate every generated image through the public constructor.
                        assert!(Sprite::new(size, height, sprite.pixels).is_ok());
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
            let enlarged: Vec<_> = (0..u32::from(detailed.height))
                .flat_map(|y| {
                    let small = &small;
                    (0..u32::from(detailed.width)).map(move |x| small.pixel(x / 2, y / 2, 1))
                })
                .collect();
            assert_ne!(enlarged, detailed.pixels, "{stage:?}");
            assert!(
                (0..u32::from(small.height)).any(|y| (0..8).any(|x| {
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
                        assert!(s.width <= 32 && s.height <= 64);
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

#[cfg(test)]
mod appearance_tests {
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
    fn animal_families_and_both_variants_have_distinct_silhouettes_at_every_stage() {
        for style in STYLES {
            for stage in STAGES {
                let mut silhouettes = std::collections::BTreeSet::new();
                for shape in 0..16 {
                    let appearance = Appearance {
                        shape,
                        body: 1,
                        accent: 11,
                    };
                    let mut silhouette = Vec::new();
                    for yaw in [0, 90, 180] {
                        let s = Sprite::voxel_with_appearance(
                            stage,
                            Expression::Idle,
                            style,
                            0,
                            yaw,
                            false,
                            appearance,
                        );
                        silhouette.extend(s.pixels.iter().map(Option::is_some));
                    }
                    assert!(
                        silhouettes.insert(silhouette),
                        "geometry duplicate {style:?} {stage:?} shape={shape}"
                    );
                }
            }
        }
    }

    #[test]
    fn animal_appearance_palette_rotations_and_moods_are_valid() {
        for style in STYLES {
            for stage in STAGES {
                for shape in 0..16 {
                    let appearance = Appearance {
                        shape,
                        body: shape,
                        accent: 15 - shape,
                    };
                    // Every mood is exercised at every stage in both styles,
                    // across the roster, including non-cardinal headings.
                    for yaw in [0, 73, 180, 287] {
                        let s = Sprite::voxel_with_appearance(
                            stage,
                            MOODS[shape as usize],
                            style,
                            3,
                            yaw,
                            true,
                            appearance,
                        );
                        assert!(s.width <= 32 && s.height <= 64);
                        assert!(s.pixels.iter().flatten().count() > 10);
                        assert!(s.pixels.contains(&None));
                        assert!(s.pixels.iter().flatten().all(|&c| c < 16));
                        assert!(Sprite::new(s.width, s.height, s.pixels).is_ok());
                    }
                }
            }
            for shape in 0..16 {
                let appearance = Appearance {
                    shape,
                    body: 1,
                    accent: 8,
                };
                for mood in MOODS {
                    let s = Sprite::voxel_with_appearance(
                        Stage::Adult,
                        mood,
                        style,
                        0,
                        41,
                        false,
                        appearance,
                    );
                    assert_eq!(
                        s.pixels,
                        Sprite::voxel_with_appearance(
                            Stage::Adult,
                            mood,
                            style,
                            4,
                            401,
                            false,
                            appearance
                        )
                        .pixels
                    );
                }
            }
        }
    }

    #[test]
    fn animal_default_wrapper_and_dead_normalization_remain_consistent() {
        for style in STYLES {
            for stage in STAGES {
                for mood in MOODS {
                    assert_eq!(
                        Sprite::voxel(stage, mood, style, 3, 315, true).pixels,
                        Sprite::voxel_with_appearance(
                            stage,
                            mood,
                            style,
                            3,
                            315,
                            true,
                            Appearance::default()
                        )
                        .pixels,
                    );
                }
            }
            for shape in 0..16 {
                let appearance = Appearance {
                    shape,
                    body: 3,
                    accent: 7,
                };
                for mood in MOODS {
                    assert_eq!(
                        Sprite::voxel_with_appearance(
                            Stage::Dead,
                            mood,
                            style,
                            0,
                            45,
                            true,
                            appearance
                        )
                        .pixels,
                        Sprite::voxel_with_appearance(
                            Stage::Adult,
                            Expression::Dead,
                            style,
                            0,
                            45,
                            false,
                            appearance
                        )
                        .pixels,
                    );
                }
            }
        }
    }

    #[test]
    fn animal_indices_and_pixel_recoloring_are_bounded_without_changing_legacy_art() {
        let invalid = Appearance {
            shape: u8::MAX,
            body: 254,
            accent: 253,
        };
        let bounded = Appearance {
            shape: 15,
            body: 14,
            accent: 13,
        };
        for style in STYLES {
            assert_eq!(
                Sprite::voxel_with_appearance(
                    Stage::Adult,
                    Expression::Happy,
                    style,
                    0,
                    0,
                    false,
                    invalid
                )
                .pixels,
                Sprite::voxel_with_appearance(
                    Stage::Adult,
                    Expression::Happy,
                    style,
                    0,
                    0,
                    false,
                    bounded
                )
                .pixels,
            );
            let s = Sprite::animated(Stage::Adult, Expression::Idle, style, 0);
            let before = s.pixels.clone();
            assert_eq!(s.recolored(1, 11).pixels, before);
            for body in 0..16 {
                let recolored = s.recolored(body, 15 - body);
                assert_eq!((recolored.width, recolored.height), (s.width, s.height));
                for (old, new) in before.iter().zip(&recolored.pixels) {
                    assert_eq!(old.is_some(), new.is_some());
                    if let Some(c) = new {
                        assert!(*c < 16);
                    }
                    if matches!(old, Some(0 | 2 | 5 | 6)) {
                        assert_eq!(old, new);
                    }
                }
            }
            assert!(s
                .recolored(255, 254)
                .pixels
                .iter()
                .flatten()
                .all(|&c| c < 16));
            assert_eq!(s.pixels, before);
        }
    }
}
