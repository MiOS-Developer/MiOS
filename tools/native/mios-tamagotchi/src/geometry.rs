// AI-hint: Original compact doll animal volumes; pure geometry, no facial glyph meshes or game state.
// AI-related: sprite.rs, game.rs, /usr/share/mios/templates/rust
// AI-functions: anatomy

use crate::game::Stage;
use crate::sprite::{Expression, SpriteStyle};

const BODY: u8 = 16;
const ACCENT: u8 = 17;
const HEAD: u8 = 18;
const MUZZLE: u8 = 19;
const MASK: u8 = 20;

/// One solid ellipsoid in final world coordinates. Positive Z is the face.
/// Materials 18..=20 alone accept face UV; fixed-color noses occlude it.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Part {
    pub center: [f32; 3],
    pub radii: [f32; 3],
    pub material: u8,
    pub fine_only: bool,
}

/// Head-space anchor, already lifted and scaled exactly like its volumes.
/// face_center/face_half describe the padded 21x7 expression texture in XY.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Head {
    pub center: [f32; 3],
    pub radii: [f32; 3],
    pub face_center: [f32; 2],
    pub face_half: [f32; 2],
}

#[derive(Clone, Debug, PartialEq)]
pub struct Anatomy {
    pub parts: Vec<Part>,
    pub head: Option<Head>,
    /// Existing child/adult canvas hint, never a request to stretch geometry.
    pub tall: bool,
}

impl Anatomy {
    fn new(tall: bool) -> Self {
        Self {
            parts: Vec::with_capacity(64),
            head: None,
            tall,
        }
    }

    fn add(&mut self, center: [f32; 3], radii: [f32; 3], material: u8) {
        self.parts.push(Part {
            center,
            radii,
            material,
            fine_only: false,
        });
    }

    fn detail(&mut self, center: [f32; 3], radii: [f32; 3], material: u8) {
        self.parts.push(Part {
            center,
            radii,
            material,
            fine_only: true,
        });
    }

    fn head(&mut self, radii: [f32; 3]) {
        let center = [0.0, 6.0, 0.0];
        self.add(center, radii, HEAD);
        self.head = Some(Head {
            center,
            radii,
            face_center: [0.0, 6.0 + 0.02 * radii[1]],
            face_half: [0.90 * radii[0], 0.50 * radii[1]],
        });
    }

    fn scale(&mut self, scale: f32) {
        for part in &mut self.parts {
            for axis in 0..3 {
                part.center[axis] *= scale;
                part.radii[axis] *= scale;
            }
        }
        if let Some(head) = &mut self.head {
            for axis in 0..3 {
                head.center[axis] *= scale;
                head.radii[axis] *= scale;
            }
            for axis in 0..2 {
                head.face_center[axis] *= scale;
                head.face_half[axis] *= scale;
            }
        }
    }

    /// Overlapping, tapering courses make a pointed ear, not a round antenna.
    fn pointed_ear(&mut self, side: f32, x: f32, height: f32, width: f32) {
        let base = 10.1;
        for i in 0..6 {
            let t = i as f32 / 5.0;
            let w = width * (1.0 - 0.87 * t);
            self.add(
                [
                    side * (x + 0.30 * t),
                    base + (height - base - 0.45) * t,
                    -0.1,
                ],
                [w, 0.90 - 0.45 * t, 1.0 - 0.65 * t],
                BODY,
            );
        }
        self.detail([side * x, 11.5, 0.90], [width * 0.43, 1.05, 0.50], 5);
    }

    fn nose(&mut self, center: [f32; 3], radii: [f32; 3], material: u8) {
        // Fixed feature material, deliberately outside face-eligible 18..20.
        self.add(center, radii, material);
    }
}

/// Geometry is independent of render resolution, yaw, clocks and gameplay RNG.
/// Sixteen-bit callers revoxelize at k=2; eight-bit callers use the unit lattice
/// after the 0.65 model scale. No quantization here can detach little paws.
pub fn anatomy(
    stage: Stage,
    expression: Expression,
    style: SpriteStyle,
    phase: usize,
    walking: bool,
    shape: u8,
) -> Anatomy {
    let phase = phase % 4;
    let stage = if expression == Expression::Dead {
        Stage::Dead
    } else {
        stage
    };
    let mut model = match stage {
        Stage::Egg => egg(),
        Stage::Dead => ghost(),
        Stage::Baby | Stage::Child | Stage::Adult => {
            let mut model = Anatomy::new(matches!(stage, Stage::Child | Stage::Adult));
            let shape = shape % 16;
            animal_head(&mut model, shape % 8, shape >= 8);
            compact_body(&mut model, stage, expression, phase, walking, shape % 8);
            animal_tail(&mut model, shape % 8, shape >= 8);
            model
        }
    };
    let life_scale = match stage {
        Stage::Baby => 0.60,
        Stage::Child => 0.85,
        _ => 1.0,
    };
    let style_scale = match style {
        SpriteStyle::EightBit => 0.65,
        SpriteStyle::SixteenBit => 1.0,
    };
    model.scale(life_scale * style_scale);
    model
}

fn egg() -> Anatomy {
    let mut model = Anatomy::new(false);
    // One closed neutral shell, never a split egg or a species-colored head.
    model.add([0.0, 1.5, 0.0], [5.8, 7.5, 4.7], 4);
    for (center, radii) in [
        ([-2.4, 3.7, 3.90], [0.65, 0.85, 0.38]),
        ([2.0, 0.4, 4.35], [0.80, 0.60, 0.35]),
        ([-1.4, -2.6, 3.80], [0.50, 0.65, 0.30]),
        ([1.1, 6.6, 2.90], [0.55, 0.55, 0.30]),
        ([3.8, 2.8, -3.0], [0.60, 0.75, 0.35]),
    ] {
        model.add(center, radii, 8);
    }
    model
}

fn ghost() -> Anatomy {
    let mut model = Anatomy::new(false);
    model.head([6.0, 5.5, 3.8]);
    // Ghost stays neutral even if the living species palette is saturated.
    model.parts[0].material = MUZZLE;
    for x in [-3.8, 0.0, 3.8] {
        model.add([x, 0.5, 0.0], [2.0, 2.1, 2.5], MUZZLE);
    }
    model.add([-6.0, 3.2, 0.0], [1.5, 1.0, 1.4], 4);
    model.add([6.0, 3.2, 0.0], [1.5, 1.0, 1.4], 4);
    // An actual open halo in the horizontal plane, with no face components.
    for i in 0..12 {
        let angle = i as f32 * std::f32::consts::TAU / 12.0;
        model.add(
            [3.2 * angle.cos(), 13.4, 1.8 * angle.sin()],
            [0.85, 0.35, 0.65],
            7,
        );
    }
    model
}

fn compact_body(
    model: &mut Anatomy,
    stage: Stage,
    expression: Expression,
    phase: usize,
    walking: bool,
    family: u8,
) {
    let baby = stage == Stage::Baby;
    let (body_y, body_ry, foot_y, foot_ry) = if baby {
        (-1.5, 2.1, -3.5, 1.05)
    } else {
        (-2.4, 2.8, -5.2, 1.5)
    };
    model.add([0.0, body_y, -0.15], [3.5, body_ry, 2.5], BODY);
    // A low, small belly spot; the clean forehead and neck have no markings.
    model.add([0.0, body_y - 0.5, 2.05], [1.7, 1.35, 0.55], ACCENT);
    let can_walk = walking && !matches!(expression, Expression::Sleeping | Expression::Dead);
    let gait = if can_walk {
        [0.0, 1.25, 0.0, -1.25][phase]
    } else {
        0.0
    };
    for side in [-1.0, 1.0] {
        // Feet overlap the body directly: no shins, floating joints or waist.
        model.add(
            [
                side * 2.0,
                foot_y + (side * gait).max(0.0) * 0.25,
                1.0 + side * gait,
            ],
            [1.55, foot_ry, 1.8],
            BODY,
        );
        let (x, y, z) = match expression {
            Expression::Eating => (2.6, 0.18 * (phase % 2) as f32, 2.6),
            Expression::Shy => (2.3, 0.4, 2.5),
            Expression::Happy | Expression::Excited | Expression::Celebrate => {
                (4.0, 0.3 + 0.25 * (phase % 2) as f32, 0.9)
            }
            Expression::Cleaning | Expression::Healing if side > 0.0 => (3.1, 1.0, 2.2),
            Expression::Playful if side < 0.0 => (4.1, 0.6, 1.1),
            Expression::Sleeping => (2.7, -1.6, 1.6),
            _ => (3.6, -1.8 + side * gait * 0.3, 0.6),
        };
        if family == 6 {
            // Small wings attach to the same common body.
            model.add([side * x, y, z - 0.4], [1.6, 1.75, 1.05], ACCENT);
        } else {
            model.add([side * x, y, z], [1.25, 1.45, 1.25], BODY);
        }
    }
}

fn animal_head(model: &mut Anatomy, family: u8, variant: bool) {
    match family {
        0 => {
            // Cat: wide cheeks, pointed ears and two small muzzle cushions.
            model.head(if variant {
                [9.6, 5.8, 4.7]
            } else {
                [8.4, 6.0, 4.5]
            });
            for side in [-1.0, 1.0] {
                model.pointed_ear(
                    side,
                    if variant { 5.9 } else { 5.5 },
                    if variant { 14.4 } else { 15.0 },
                    if variant { 2.0 } else { 2.15 },
                );
                model.add([side * 1.15, 4.8, 4.2], [1.5, 0.90, 0.85], MUZZLE);
                for y in [3.8, 4.5] {
                    model.detail([side * 5.5, y, 3.5], [0.95, 0.13, 0.45], 4);
                }
            }
            // Three overlapping courses give a small downward triangular tip.
            model.nose([0.0, 5.45, 5.0], [0.70, 0.27, 0.45], 5);
            model.nose([0.0, 5.20, 5.0], [0.40, 0.25, 0.42], 5);
            model.nose([0.0, 5.02, 5.0], [0.18, 0.18, 0.35], 5);
        }
        1 => {
            model.head(if variant {
                [7.6, 5.9, 4.4]
            } else {
                [7.9, 6.0, 4.2]
            });
            for side in [-1.0, 1.0] {
                model.pointed_ear(
                    side,
                    if variant { 5.6 } else { 4.9 },
                    15.2,
                    if variant { 1.5 } else { 1.8 },
                );
                model.add([side * 3.1, 4.3, 3.5], [2.6, 1.6, 1.2], MUZZLE);
            }
            // Forward taper makes a true fox snout in profile.
            model.add([0.0, 4.9, 4.3], [2.4, 1.45, 1.6], MUZZLE);
            model.add([0.0, 5.0, 5.5], [1.5, 0.95, 1.2], MUZZLE);
            model.nose([0.0, 5.25, 6.45], [0.65, 0.45, 0.55], 0);
        }
        2 => {
            model.head(if variant {
                [8.1, 6.1, 4.8]
            } else {
                [8.5, 6.0, 4.7]
            });
            for side in [-1.0, 1.0] {
                model.add([side * 6.4, 11.5, -0.25], [2.15, 2.15, 1.35], BODY);
                model.detail([side * 6.4, 11.5, 1.0], [1.15, 1.2, 0.35], ACCENT);
                // Broad connected mask volumes receive the same face texture.
                model.add([side * 3.2, 6.7, 3.2], [3.4, 2.35, 1.7], MASK);
            }
            model.add([0.0, 4.4, 4.1], [3.0, 1.5, 1.2], MUZZLE);
            model.nose([0.0, 5.25, 5.15], [0.85, 0.65, 0.65], 0);
            if variant {
                model.add([-0.9, 12.3, -0.2], [2.8, 0.45, 1.7], ACCENT);
                model.add([1.1, 12.7, -0.2], [2.0, 0.40, 1.35], ACCENT);
                model.detail([1.9, 13.0, -0.3], [0.25, 0.55, 0.25], 11);
            }
        }
        3 => {
            model.head(if variant {
                [7.6, 6.0, 4.5]
            } else {
                [7.8, 5.9, 4.4]
            });
            model.add([-4.4, 12.0, -0.3], [1.65, 3.45, 1.2], BODY);
            model.detail([-4.4, 12.3, 0.65], [0.65, 2.35, 0.35], 5);
            if variant {
                // A joined bent ear droops outward, rather than recoloring.
                model.add([4.2, 11.6, -0.3], [1.7, 2.5, 1.2], BODY);
                model.add([5.4, 12.7, -0.25], [1.8, 1.35, 1.15], BODY);
                model.add([6.5, 11.4, -0.2], [1.1, 1.9, 1.05], BODY);
                model.detail([6.4, 11.5, 0.65], [0.45, 1.15, 0.3], 5);
            } else {
                model.add([4.4, 12.0, -0.3], [1.65, 3.45, 1.2], BODY);
                model.detail([4.4, 12.3, 0.65], [0.65, 2.35, 0.35], 5);
            }
            model.add([0.0, 4.7, 4.0], [2.4, 1.05, 0.9], MUZZLE);
            model.nose([0.0, 5.4, 4.9], [0.65, 0.45, 0.5], 5);
        }
        4 => {
            model.head(if variant {
                [8.3, 6.2, 4.9]
            } else {
                [8.0, 6.0, 4.8]
            });
            for side in [-1.0, 1.0] {
                model.add(
                    [side * 6.1, if variant { 11.6 } else { 11.1 }, -0.4],
                    [if variant { 2.4 } else { 2.1 }, 2.1, 1.55],
                    BODY,
                );
                model.detail([side * 6.1, 11.4, 0.9], [1.1, 1.1, 0.4], ACCENT);
            }
            model.add(
                [0.0, 4.8, 4.3],
                [if variant { 3.4 } else { 2.8 }, 1.7, 1.35],
                MUZZLE,
            );
            model.nose([0.0, 5.5, 5.55], [0.95, 0.65, 0.65], 0);
        }
        5 => {
            model.head(if variant {
                [8.7, 5.4, 4.4]
            } else {
                [9.0, 5.3, 4.1]
            });
            if let Some(head) = &mut model.head {
                head.face_center[1] = head.center[1] + 0.10 * head.radii[1];
                head.face_half[1] = 0.62 * head.radii[1];
            }
            // Brow lobes are continuous head tissue, with no eyeball meshes.
            for side in [-1.0, 1.0] {
                model.add([side * 4.9, 10.1, -0.25], [2.8, 1.8, 2.1], HEAD);
            }
            if variant {
                // Low kappa dish and a short, solid bill.
                model.add([0.0, 12.0, -0.8], [3.3, 0.55, 2.25], ACCENT);
                model.add([0.0, 12.4, -0.8], [2.6, 0.18, 1.65], 4);
                model.nose([0.0, 5.0, 4.5], [2.4, 0.6, 1.3], 7);
            } else {
                // Tiny raised nostrils sit together at the center, not as eyes.
                for x in [-0.55, 0.55] {
                    model.nose([x, 5.4, 4.05], [0.20, 0.18, 0.20], 11);
                }
            }
        }
        6 => {
            model.head(if variant {
                [7.4, 6.5, 4.8]
            } else {
                [7.0, 6.4, 4.6]
            });
            if variant {
                for (x, y, rx) in [(-1.5, 12.1, 1.0), (0.0, 12.55, 0.85), (1.2, 12.3, 0.75)] {
                    model.add([x, y, -0.5], [rx, 1.2, 1.1], ACCENT);
                }
                // A broad short beak differs physically from the pointed one.
                model.nose([0.0, 5.1, 4.9], [1.9, 0.65, 1.15], 7);
            } else {
                model.nose([0.0, 5.3, 4.7], [1.5, 0.75, 1.1], 7);
                model.nose([0.0, 5.05, 5.55], [0.85, 0.45, 0.85], 7);
                model.nose([0.0, 4.85, 6.1], [0.35, 0.25, 0.45], 7);
            }
        }
        _ => {
            model.head(if variant {
                [7.6, 6.1, 4.8]
            } else {
                [7.4, 6.0, 4.7]
            });
            model.add(
                [0.0, 4.5, 4.1],
                [if variant { 3.4 } else { 2.9 }, 1.5, 1.1],
                MUZZLE,
            );
            model.nose([0.0, 5.3, 5.1], [0.8, 0.5, 0.65], 11);
            // Shell is a low rear appendage on the common compact body.
            model.add(
                [0.0, -2.1, -2.25],
                [if variant { 4.8 } else { 4.4 }, 3.3, 2.5],
                ACCENT,
            );
            for x in [-2.1, 0.0, 2.1] {
                model.detail([x, -1.7, -4.4], [1.0, 1.7, 0.55], 11);
            }
            if variant {
                for y in [-3.5, -1.5, 0.5] {
                    model.add([0.0, y, -4.5], [0.75, 0.85, 1.15], BODY);
                }
            }
        }
    }
}

fn animal_tail(model: &mut Anatomy, family: u8, variant: bool) {
    match family {
        0 => {
            // Pelvic curl stays below the jaw: no tall tail to fake head ratio.
            let curl: &[[f32; 3]] = if variant {
                &[
                    [-2.5, -3.0, -2.0],
                    [-4.0, -3.0, -2.8],
                    [-5.4, -2.3, -3.0],
                    [-5.8, -0.9, -3.0],
                    [-4.8, -0.1, -3.0],
                ]
            } else {
                &[
                    [2.5, -3.0, -2.0],
                    [4.2, -3.0, -2.8],
                    [5.8, -2.3, -3.0],
                    [6.3, -0.8, -3.0],
                    [5.7, 0.5, -3.0],
                    [4.6, 0.6, -3.0],
                ]
            };
            for &center in curl {
                model.add(center, [0.95, 0.95, 0.95], BODY);
            }
        }
        1 => {
            let x = if variant { -4.8 } else { 4.8 };
            model.add([x * 0.55, -2.7, -2.2], [2.0, 1.5, 1.5], BODY);
            model.add(
                [x, -2.1, -3.2],
                [if variant { 3.1 } else { 2.7 }, 2.1, 2.0],
                BODY,
            );
            model.add([x * 1.38, -1.3, -3.3], [1.6, 1.7, 1.65], 4);
        }
        2 => {
            let side = if variant { -1.0 } else { 1.0 };
            for i in 0..5 {
                model.add(
                    [side * (2.6 + i as f32 * 0.85), -3.0 + i as f32 * 0.28, -2.5],
                    [1.2, 1.25, 1.15],
                    if i % 2 == 0 { BODY } else { ACCENT },
                );
            }
        }
        3 => model.add(
            [if variant { -2.7 } else { 2.5 }, -2.7, -2.9],
            if variant {
                [1.9, 1.5, 1.7]
            } else {
                [1.6, 1.6, 1.6]
            },
            4,
        ),
        4 => model.add(
            [if variant { -1.6 } else { 1.2 }, -2.9, -2.5],
            if variant {
                [1.1, 1.0, 1.25]
            } else {
                [0.85, 0.9, 1.0]
            },
            BODY,
        ),
        5 => {
            // Frog rump is low and broad; the kappa variant has a little nub.
            model.add(
                [0.0, -3.3, -2.1],
                if variant {
                    [1.25, 0.8, 1.7]
                } else {
                    [1.8, 1.0, 0.85]
                },
                BODY,
            );
        }
        6 => {
            for side in [-1.0, 1.0] {
                model.add(
                    [side * if variant { 1.5 } else { 0.85 }, -3.0, -3.0],
                    if variant {
                        [1.2, 1.0, 2.1]
                    } else {
                        [0.85, 0.85, 1.6]
                    },
                    ACCENT,
                );
            }
        }
        _ => {
            model.add(
                [if variant { -1.7 } else { 1.5 }, -3.6, -3.4],
                [0.9, 0.85, if variant { 2.4 } else { 1.9 }],
                BODY,
            );
            model.add(
                [if variant { -2.0 } else { 1.7 }, -3.6, -5.0],
                [0.45, 0.45, 0.85],
                BODY,
            );
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn adult(shape: u8) -> Anatomy {
        anatomy(
            Stage::Adult,
            Expression::Idle,
            SpriteStyle::SixteenBit,
            0,
            false,
            shape,
        )
    }

    fn y_bounds(parts: &[Part]) -> (f32, f32) {
        parts
            .iter()
            .fold((f32::INFINITY, f32::NEG_INFINITY), |(lo, hi), p| {
                (
                    lo.min(p.center[1] - p.radii[1]),
                    hi.max(p.center[1] + p.radii[1]),
                )
            })
    }

    #[test]
    fn closed_neutral_egg_ignores_species_pose_and_gait() {
        let egg = anatomy(
            Stage::Egg,
            Expression::Idle,
            SpriteStyle::SixteenBit,
            0,
            false,
            0,
        );
        assert!(egg.head.is_none());
        assert_eq!(egg.parts[0].material, 4);
        assert_eq!(egg.parts[0].radii, [5.8, 7.5, 4.7]);
        for shape in 0..16 {
            assert_eq!(
                egg,
                anatomy(
                    Stage::Egg,
                    Expression::Eating,
                    SpriteStyle::SixteenBit,
                    usize::MAX,
                    true,
                    shape
                )
            );
        }
    }

    #[test]
    fn sixteen_original_profiles_change_volumes_not_only_materials() {
        let signature = |m: Anatomy| {
            m.parts
                .into_iter()
                .map(|p| (p.center, p.radii))
                .collect::<Vec<_>>()
        };
        for a in 0..16 {
            for b in a + 1..16 {
                assert_ne!(
                    signature(adult(a)),
                    signature(adult(b)),
                    "{a} duplicates {b}"
                );
            }
        }
        for family in 0..8 {
            let a = adult(family);
            let b = adult(family + 8);
            assert_ne!(a.head.unwrap().radii, b.head.unwrap().radii);
            let tails = |m: Anatomy| {
                m.parts
                    .into_iter()
                    .filter(|p| p.center[2] < -1.5 && p.center[1] < 1.0)
                    .map(|p| (p.center, p.radii))
                    .collect::<Vec<_>>()
            };
            assert_ne!(
                tails(a),
                tails(b),
                "family {family} needs a physical tail variant"
            );
        }
    }

    #[test]
    fn adult_head_and_ears_are_sixty_to_seventy_percent_of_height() {
        for shape in 0..16 {
            let m = adult(shape);
            let h = m.head.unwrap();
            let (bottom, top) = y_bounds(&m.parts);
            let head_bottom = h.center[1] - h.radii[1];
            let ratio = (top - head_bottom) / (top - bottom);
            assert!((0.60..=0.70).contains(&ratio), "shape {shape}: {ratio}");
            if shape % 8 == 0 {
                assert!(h.radii[0] >= h.radii[1]);
            }
            // The tail never contributes the model's top or bottom.
            for p in m
                .parts
                .iter()
                .filter(|p| p.center[2] < -1.5 && p.center[1] < 1.0)
            {
                assert!(p.center[1] - p.radii[1] >= bottom);
                assert!(p.center[1] + p.radii[1] < h.center[1]);
            }
        }
    }

    #[test]
    fn common_round_body_has_attached_stub_paws_and_no_shins() {
        for shape in 0..16 {
            let m = adult(shape);
            assert!(m.parts.iter().any(|p| p.material == BODY
                && p.center == [0.0, -2.4, -0.15]
                && p.radii == [3.5, 2.8, 2.5]));
            let feet = m
                .parts
                .iter()
                .filter(|p| p.center[1] == -5.2)
                .collect::<Vec<_>>();
            assert_eq!(feet.len(), 2);
            assert!(feet[0].center[0] * feet[1].center[0] < 0.0);
            for p in feet {
                assert_eq!(p.radii, [1.55, 1.5, 1.8]);
                assert!(p.center[1] + p.radii[1] > -5.2); // overlap body bottom
                assert!((p.radii[1] / p.radii[0]) < 1.1);
            }
        }
    }

    #[test]
    fn uv_anchor_stays_inside_head_and_species_noses_occlude_it() {
        for shape in 0..16 {
            let m = adult(shape);
            let h = m.head.unwrap();
            let dx = (h.face_center[0] - h.center[0]).abs() + h.face_half[0];
            let dy = (h.face_center[1] - h.center[1]).abs() + h.face_half[1];
            // The padded texture's corners are clipped by the actual head
            // surface. Its center and four axial extrema stay inside the
            // ellipsoid; an expanded frog expression need not fit blank corners.
            for point in [
                h.face_center,
                [h.face_center[0] - h.face_half[0], h.face_center[1]],
                [h.face_center[0] + h.face_half[0], h.face_center[1]],
                [h.face_center[0], h.face_center[1] - h.face_half[1]],
                [h.face_center[0], h.face_center[1] + h.face_half[1]],
            ] {
                assert!(
                    ((point[0] - h.center[0]) / h.radii[0]).powi(2)
                        + ((point[1] - h.center[1]) / h.radii[1]).powi(2)
                        <= 1.0
                );
            }
            assert!(dx <= h.radii[0] && dy <= h.radii[1]);
            assert!(m.parts.iter().any(|p| p.material < 16
                && p.center[0].abs() < 1.0
                && (4.7..=5.6).contains(&p.center[1])
                && p.center[2] > h.radii[2] - 0.2));
            assert!(!m.parts.iter().any(|p| p.material < 16
                && p.center[0].abs() > 1.0
                && (6.0..=9.0).contains(&p.center[1])
                && p.center[2] > 2.0));
        }
    }

    #[test]
    fn frog_brows_form_a_low_connected_crown_and_face_uses_its_height() {
        for shape in [5, 13] {
            let m = adult(shape);
            let h = m.head.unwrap();
            assert!(h.radii[0] / h.radii[1] > 1.6);
            let brows: Vec<_> = m
                .parts
                .iter()
                .filter(|p| p.material == HEAD && p.center[1] > h.center[1])
                .collect();
            assert_eq!(brows.len(), 2);
            for brow in brows {
                assert!(brow.center[1] + brow.radii[1] <= 12.0);
                assert!(brow.radii[0] > brow.radii[1]);
                // The lobe center is in the main head's XY section, proving
                // these are joined brow mounds rather than detached round ears.
                assert!(
                    (brow.center[0] / h.radii[0]).powi(2)
                        + ((brow.center[1] - h.center[1]) / h.radii[1]).powi(2)
                        < 1.0
                );
            }
            assert!((h.face_half[1] / h.radii[1] - 0.62).abs() < 0.0001);
            assert!(((h.face_center[1] - h.center[1]) / h.radii[1] - 0.10).abs() < 0.0001);
            // The unchanged physical nose/bill stays below the expression
            // center and above its lower occupied glyph band.
            let lower_glyph_band = h.face_center[1] - h.face_half[1] * 5.0 / 7.0;
            assert!(m.parts.iter().any(|p| p.material < 16
                && p.center[0].abs() < 1.0
                && p.center[2] > h.radii[2] - 0.2
                && p.center[1] > lower_glyph_band
                && p.center[1] < h.face_center[1]));
        }
    }

    #[test]
    fn lifecycle_scales_without_a_second_head_lift() {
        let baby = anatomy(
            Stage::Baby,
            Expression::Idle,
            SpriteStyle::SixteenBit,
            0,
            false,
            0,
        );
        let child = anatomy(
            Stage::Child,
            Expression::Idle,
            SpriteStyle::SixteenBit,
            0,
            false,
            0,
        );
        let adult = adult(0);
        assert_eq!(baby.head.unwrap().center[1], 6.0 * 0.60);
        assert_eq!(child.head.unwrap().center[1], 6.0 * 0.85);
        assert_eq!(adult.head.unwrap().center[1], 6.0);
        assert_ne!(baby.parts, child.parts);
        assert_ne!(child.parts, adult.parts);
        let dead = anatomy(
            Stage::Dead,
            Expression::Idle,
            SpriteStyle::SixteenBit,
            0,
            true,
            0,
        );
        assert!(dead.head.is_some());
        assert!(!dead.tall);
        assert_eq!(
            dead,
            anatomy(
                Stage::Adult,
                Expression::Dead,
                SpriteStyle::SixteenBit,
                3,
                false,
                15
            )
        );
    }

    #[test]
    fn bundled_baby_and_older_paws_shuffle_at_both_style_scales() {
        for stage in [Stage::Baby, Stage::Child, Stage::Adult] {
            for style in [SpriteStyle::EightBit, SpriteStyle::SixteenBit] {
                let poses: Vec<_> = [0, 1, 3]
                    .into_iter()
                    .map(|phase| anatomy(stage, Expression::Idle, style, phase, true, 0))
                    .collect();
                assert_ne!(poses[0].parts, poses[1].parts);
                assert_ne!(poses[1].parts, poses[2].parts);
                let scale = match stage {
                    Stage::Baby => 0.60,
                    Stage::Child => 0.85,
                    _ => 1.0,
                } * if style == SpriteStyle::EightBit {
                    0.65
                } else {
                    1.0
                };
                for side in [-1.0, 1.0] {
                    let paw_z = |m: &Anatomy| {
                        m.parts
                            .iter()
                            .find(|p| {
                                (p.center[0] - side * 2.0 * scale).abs() < 0.0001
                                    && (p.radii[2] - 1.8 * scale).abs() < 0.0001
                                    && p.center[1] < -scale
                            })
                            .unwrap()
                            .center[2]
                    };
                    assert!((paw_z(&poses[1]) - paw_z(&poses[0])).abs() >= 0.48);
                    assert!((paw_z(&poses[1]) - paw_z(&poses[2])).abs() >= 0.96);
                }
            }
        }
    }

    #[test]
    fn styles_scale_volumes_for_independent_revoxelization() {
        for shape in 0..16 {
            let fine = adult(shape);
            let coarse = anatomy(
                Stage::Adult,
                Expression::Idle,
                SpriteStyle::EightBit,
                0,
                false,
                shape,
            );
            assert_eq!(fine.parts.len(), coarse.parts.len());
            for (a, b) in fine.parts.iter().zip(&coarse.parts) {
                for axis in 0..3 {
                    assert!((b.center[axis] - a.center[axis] * 0.65).abs() < 0.0001);
                    assert!((b.radii[axis] - a.radii[axis] * 0.65).abs() < 0.0001);
                }
            }
        }
    }

    #[test]
    fn bounded_inputs_and_four_phase_motion_never_make_invalid_solids() {
        let expressions = [
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
        for stage in [
            Stage::Egg,
            Stage::Baby,
            Stage::Child,
            Stage::Adult,
            Stage::Dead,
        ] {
            for style in [SpriteStyle::EightBit, SpriteStyle::SixteenBit] {
                for expression in expressions {
                    for shape in 0..16 {
                        let m = anatomy(stage, expression, style, usize::MAX, true, shape);
                        assert!(m.parts.len() < 80);
                        assert_eq!(m, anatomy(stage, expression, style, 3, true, shape + 16));
                        for p in m.parts {
                            assert!(p.material <= MASK);
                            for axis in 0..3 {
                                assert!(p.center[axis].is_finite() && p.center[axis].abs() < 24.0);
                                assert!(
                                    p.radii[axis].is_finite()
                                        && p.radii[axis] > 0.0
                                        && p.radii[axis] < 12.0
                                );
                            }
                        }
                    }
                }
            }
        }
        for stage in [Stage::Egg, Stage::Dead] {
            assert_eq!(
                anatomy(
                    stage,
                    Expression::Idle,
                    SpriteStyle::SixteenBit,
                    0,
                    false,
                    0
                ),
                anatomy(stage, Expression::Idle, SpriteStyle::SixteenBit, 1, true, 0)
            );
        }
        assert_eq!(
            anatomy(
                Stage::Adult,
                Expression::Sleeping,
                SpriteStyle::SixteenBit,
                0,
                false,
                0
            ),
            anatomy(
                Stage::Adult,
                Expression::Sleeping,
                SpriteStyle::SixteenBit,
                0,
                true,
                0
            )
        );
        assert_ne!(
            anatomy(
                Stage::Adult,
                Expression::Idle,
                SpriteStyle::SixteenBit,
                0,
                true,
                0
            ),
            anatomy(
                Stage::Adult,
                Expression::Idle,
                SpriteStyle::SixteenBit,
                1,
                true,
                0
            )
        );
    }
}
