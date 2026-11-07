// AI-hint: Deterministic CPU room solids, orthographic camera and depth-tested surface metadata.
// AI-related: render.rs HabitatView, sprite.rs, usr/share/mios/mios.toml [tamagotchi.world]
use crate::{render::HabitatView, sprite::SpriteStyle};
use serde::Deserialize;

#[derive(Clone, Copy, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorldRules {
    pub enabled: bool,
    pub width_tiles: u8,
    pub depth_tiles: u8,
    pub wall_height: u8,
    pub pet_height_percent: u16,
    pub default_yaw_degrees: u16,
    pub orbit_step_degrees: u16,
    /// Fixed isometric pitch, never a control for interactive tilt.
    pub elevation_degrees: u16,
    pub follow_percent: u8,
    pub backdrop: u8,
}
impl Default for WorldRules {
    fn default() -> Self {
        Self {
            enabled: true,
            width_tiles: 8,
            depth_tiles: 8,
            wall_height: 4,
            pet_height_percent: 65,
            default_yaw_degrees: 45,
            orbit_step_degrees: 45,
            elevation_degrees: 30,
            follow_percent: 100,
            backdrop: 6,
        }
    }
}
impl WorldRules {
    pub fn validate(&self) -> Result<(), String> {
        if !(4..=24).contains(&self.width_tiles) || !(4..=24).contains(&self.depth_tiles) {
            return Err("tamagotchi.world: width_tiles and depth_tiles must be 4..=24".into());
        }
        if !(2..=8).contains(&self.wall_height)
            || !(10..=150).contains(&self.pet_height_percent)
            || self.default_yaw_degrees >= 360
            || !(1..=180).contains(&self.orbit_step_degrees)
            || self.elevation_degrees != 30
            || self.follow_percent > 100
            || self.backdrop > 15
        {
            return Err(
                "tamagotchi.world: invalid height, yaw, step, fixed elevation, follow or palette"
                    .into(),
            );
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug)]
pub struct WorldCamera {
    pub yaw_degrees: u16,
    pub zoom_percent: u16,
    /// Normalized room coordinates, 0..=1000, as supplied by autonomous simulation.
    pub position: (u16, u16),
    /// Packed pixel width divided by packed pixel height.
    pub pixel_aspect: f32,
    pub detail: SpriteStyle,
    pub pan_permille: (i16, i16),
    pub elevation_radians: Option<f32>,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct WorldPixel {
    pub palette: u8,
    /// Unit view-space normal: screen right, screen up, toward viewer.
    pub normal: [f32; 3],
    pub occlusion: u8,
    /// World-view units, increasing toward the viewer; not scaled by zoom.
    pub depth: f32,
}

/// Model-space geometry at the exact visible depth-buffer winner.
/// Coordinates use room tiles, independent of zoom, pan and camera rotation.
/// This is continuous surface metadata, not a claim that the room mesh is voxelized.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct WorldSurface {
    pub position: [f32; 3],
    pub normal: [f32; 3],
}

impl WorldSurface {
    /// Pick the lattice cell inside this surface, rather than a neighbouring
    /// outside cell. Values within 1e-4 lattice cells of an integer snap to that
    /// edge; positive model-normal components then select its lower cell.
    /// Identical frames are bit-deterministic. This edge rule also tolerates
    /// small inverse-projection errors, without promising cross-camera bit
    /// equality for continuous floating coordinates.
    pub fn lattice_cell(&self, pitch: f32) -> Option<[i32; 3]> {
        if !pitch.is_finite() || pitch <= 0.0 {
            return None;
        }
        let mut cell = [0; 3];
        for (axis, out) in cell.iter_mut().enumerate() {
            let q = f64::from(self.position[axis]) / f64::from(pitch);
            if !q.is_finite() || !self.normal[axis].is_finite() {
                return None;
            }
            let edge = q.round();
            let inside = if (q - edge).abs() <= 1e-4 {
                edge - f64::from(u8::from(self.normal[axis] > 0.0))
            } else {
                q.floor()
            };
            if inside < f64::from(i32::MIN) || inside > f64::from(i32::MAX) {
                return None;
            }
            *out = inside as i32;
        }
        Some(cell)
    }
}

#[derive(Debug)]
pub struct WorldFrame {
    pub width: u16,
    pub height: u16,
    pub pixels: Vec<Option<WorldPixel>>,
    /// Packed screen X, packed screen Y, world-view depth at the pet's feet.
    pub pet_anchor: [f32; 3],
    /// Horizontal packed pixels per world tile.
    pub scale: f32,
    pub pet_height_pixels: f32,
    projection: Option<SurfaceProjection>,
}
impl WorldFrame {
    /// All storage is bounded by the visible packed viewport, independently of zoom.
    /// Invalid public inputs are sanitized here; configuration loading uses validate.
    pub fn render(
        rules: &WorldRules,
        habitat: Option<HabitatView<'_>>,
        camera: WorldCamera,
        size: (u16, u16),
    ) -> Self {
        let width = size.0.min(1024);
        let height = size.1.min(1024);
        let mut frame = Self {
            width,
            height,
            pixels: vec![None; usize::from(width) * usize::from(height)],
            pet_anchor: [0.0; 3],
            scale: 0.0,
            pet_height_pixels: 0.0,
            projection: None,
        };
        if width == 0 || height == 0 {
            return frame;
        }
        let w = f32::from(rules.width_tiles.clamp(4, 24));
        let d = f32::from(rules.depth_tiles.clamp(4, 24));
        let h = f32::from(rules.wall_height.clamp(2, 8));
        let aspect = if camera.pixel_aspect.is_finite() {
            camera.pixel_aspect.clamp(0.25, 4.0)
        } else {
            1.0
        };
        let zoom = f32::from(camera.zoom_percent.clamp(25, 800)) / 100.0;
        let mut view = View::new(camera.yaw_degrees, aspect);
        if let Some(pitch) = camera.elevation_radians.filter(|p| p.is_finite()) {
            view.elevation = pitch.clamp(0.01, 89_f32.to_radians());
        }
        let pet = [
            0.65 + f32::from(camera.position.0.min(1000)) / 1000.0 * (w - 1.3),
            0.035,
            0.65 + f32::from(camera.position.1.min(1000)) / 1000.0 * (d - 1.3),
        ];
        let mut low = [f32::INFINITY; 2];
        let mut high = [f32::NEG_INFINITY; 2];
        for x in [-0.2, w + 0.2] {
            for z in [-0.2, d + 0.2] {
                for y in [-0.28, h + 0.14] {
                    let p = view.rotate([x, y, z]);
                    low[0] = low[0].min(p[0]);
                    high[0] = high[0].max(p[0]);
                    low[1] = low[1].min(-p[1] * aspect);
                    high[1] = high[1].max(-p[1] * aspect);
                }
            }
        }
        frame.scale = ((f32::from(width) * 0.92 / (high[0] - low[0]))
            .min(f32::from(height) * 0.92 / (high[1] - low[1])))
        .max(0.001)
            * zoom;
        let center = [(low[0] + high[0]) * 0.5, (low[1] + high[1]) * 0.5];
        let p = view.rotate(pet);
        let follow = f32::from(rules.follow_percent.min(100)) / 100.0;
        // Follow in exactly the same projected space as room geometry.
        let target = [
            center[0] + (p[0] - center[0]) * follow,
            center[1] + (-p[1] * aspect - center[1]) * follow,
        ];
        let projection = Projection {
            view,
            scale: frame.scale,
            offset: [
                f32::from(width)
                    * (0.5 - f32::from(camera.pan_permille.0.clamp(-400, 400)) / 1000.0)
                    - target[0] * frame.scale,
                f32::from(height)
                    * (0.62 - f32::from(camera.pan_permille.1.clamp(-400, 400)) / 1000.0)
                    - target[1] * frame.scale,
            ],
        };
        frame.projection = Some(SurfaceProjection::new(projection));
        frame.pet_anchor = projection.point(pet);
        // The Mon and room share world units. A viewport-relative pet size
        // would make the same Mon grow when the room becomes wider or rotates.
        frame.pet_height_pixels = h * f32::from(rules.pet_height_percent.clamp(10, 150)) / 100.0
            * view.elevation.cos()
            * frame.scale
            * aspect;
        if !rules.enabled {
            frame.projection = None;
            return frame;
        }
        let scene = room(w, d, h, camera.detail, habitat, view);
        for triangle in scene.triangles {
            frame.triangle(triangle, projection);
        }
        frame
    }

    pub fn sample(&self, x: u32, y: u32) -> Option<WorldPixel> {
        if x >= u32::from(self.width) || y >= u32::from(self.height) {
            return None;
        }
        self.pixels[usize::try_from(y).ok()? * usize::from(self.width) + usize::try_from(x).ok()?]
    }

    /// Unproject the winning packed pixel centre into model space lazily.
    /// No viewport-sized metadata allocation or per-triangle work is required.
    /// Floating point reconstruction is approximate; callers should use a chosen
    /// model-space lattice, rather than hash raw coordinates or view normals.
    pub fn sample_surface(&self, x: u32, y: u32) -> Option<WorldSurface> {
        let pixel = self.sample(x, y)?;
        let projection = self.projection?;
        let position = projection.unproject([x as f32 + 0.5, y as f32 + 0.5, pixel.depth]);
        let normal = projection.unrotate(pixel.normal);
        if !position.iter().chain(normal.iter()).all(|v| v.is_finite()) {
            return None;
        }
        Some(WorldSurface { position, normal })
    }

    /// Packed-pixel bounding extent of a model-aligned cube with this tile pitch.
    /// The caller chooses its lattice pitch; no new room/detail setting is implied.
    /// Returns None for empty/disabled frames or invalid (non-positive) pitches.
    pub fn lattice_extent_pixels(&self, pitch: f32) -> Option<[f32; 2]> {
        if !pitch.is_finite() || pitch <= 0.0 {
            return None;
        }
        let p = self.projection?.projection;
        let horizontal = p.view.cos.abs() + p.view.sin.abs();
        let (sp, cp) = p.view.elevation.sin_cos();
        let extent = [
            pitch * p.scale * horizontal,
            pitch * p.scale * p.view.aspect * (cp.abs() + sp.abs() * horizontal),
        ];
        extent.iter().all(|v| v.is_finite()).then_some(extent)
    }

    fn triangle(&mut self, triangle: Triangle, projection: Projection) {
        let normal = projection.view.rotate(triangle.normal);
        // Only actual front-facing surfaces enter the depth buffer.
        if normal[2] <= 0.00001 {
            return;
        }
        let p = triangle.points.map(|point| projection.point(point));
        let area = edge(p[0], p[1], p[2]);
        if !area.is_finite() || area.abs() < 0.000001 {
            return;
        }
        let min_x = p
            .iter()
            .map(|v| v[0])
            .fold(f32::INFINITY, f32::min)
            .floor()
            .max(0.0)
            .min(f32::from(self.width)) as u16;
        let max_x = p
            .iter()
            .map(|v| v[0])
            .fold(f32::NEG_INFINITY, f32::max)
            .ceil()
            .max(0.0)
            .min(f32::from(self.width)) as u16;
        let min_y = p
            .iter()
            .map(|v| v[1])
            .fold(f32::INFINITY, f32::min)
            .floor()
            .max(0.0)
            .min(f32::from(self.height)) as u16;
        let max_y = p
            .iter()
            .map(|v| v[1])
            .fold(f32::NEG_INFINITY, f32::max)
            .ceil()
            .max(0.0)
            .min(f32::from(self.height)) as u16;
        for y in min_y..max_y {
            for x in min_x..max_x {
                let q = [f32::from(x) + 0.5, f32::from(y) + 0.5, 0.0];
                let a = edge(p[1], p[2], q) / area;
                let b = edge(p[2], p[0], q) / area;
                let c = 1.0 - a - b;
                if a < -0.00001 || b < -0.00001 || c < -0.00001 {
                    continue;
                }
                let depth = a * p[0][2] + b * p[1][2] + c * p[2][2];
                let pixel = WorldPixel {
                    palette: triangle.palette,
                    normal,
                    occlusion: triangle.occlusion,
                    depth,
                };
                let at = usize::from(y) * usize::from(self.width) + usize::from(x);
                if self.pixels[at].is_none_or(|old| wins(pixel, old)) {
                    self.pixels[at] = Some(pixel);
                }
            }
        }
    }
}
fn wins(new: WorldPixel, old: WorldPixel) -> bool {
    // Stable total ordering also resolves exactly coincident surfaces independently
    // of mesh submission order. No near-depth tolerance hides a foreground face.
    new.depth
        .total_cmp(&old.depth)
        .then(new.palette.cmp(&old.palette))
        .then(new.occlusion.cmp(&old.occlusion))
        .then(new.normal[0].total_cmp(&old.normal[0]))
        .then(new.normal[1].total_cmp(&old.normal[1]))
        .then(new.normal[2].total_cmp(&old.normal[2]))
        .is_gt()
}
fn edge(a: [f32; 3], b: [f32; 3], p: [f32; 3]) -> f32 {
    (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
}
#[derive(Clone, Copy, Debug)]
struct View {
    sin: f32,
    cos: f32,
    aspect: f32,
    elevation: f32,
}
impl View {
    fn new(yaw: u16, aspect: f32) -> Self {
        let (sin, cos) = f32::from(yaw % 360).to_radians().sin_cos();
        Self {
            sin,
            cos,
            aspect,
            elevation: 30.0_f32.to_radians(),
        }
    }
    fn rotate(self, [x, y, z]: [f32; 3]) -> [f32; 3] {
        let rx = self.cos * x + self.sin * z;
        let rz = -self.sin * x + self.cos * z;
        let (sp, cp) = self.elevation.sin_cos();
        [rx, cp * y - sp * rz, sp * y + cp * rz]
    }

    fn unrotate(self, [x, y, z]: [f32; 3]) -> [f32; 3] {
        let (sp, cp) = self.elevation.sin_cos();
        let ry = cp * y + sp * z;
        let rz = -sp * y + cp * z;
        [
            self.cos * x - self.sin * rz,
            ry,
            self.sin * x + self.cos * rz,
        ]
    }
}
#[derive(Clone, Copy, Debug)]
struct Projection {
    view: View,
    scale: f32,
    offset: [f32; 2],
}
impl Projection {
    fn point(self, point: [f32; 3]) -> [f32; 3] {
        let p = self.view.rotate(point);
        [
            self.offset[0] + p[0] * self.scale,
            self.offset[1] - p[1] * self.scale * self.view.aspect,
            p[2],
        ]
    }
}
/// One inverse basis per frame; per-pixel queries use multiply/add operations
/// and do not allocate or compute trigonometric functions.
#[derive(Clone, Copy, Debug)]
struct SurfaceProjection {
    projection: Projection,
    inverse: [[f32; 3]; 3],
}
impl SurfaceProjection {
    fn new(projection: Projection) -> Self {
        Self {
            projection,
            inverse: [[1., 0., 0.], [0., 1., 0.], [0., 0., 1.]]
                .map(|axis| projection.view.unrotate(axis)),
        }
    }

    fn unrotate(self, point: [f32; 3]) -> [f32; 3] {
        std::array::from_fn(|axis| {
            self.inverse[0][axis] * point[0]
                + self.inverse[1][axis] * point[1]
                + self.inverse[2][axis] * point[2]
        })
    }

    fn unproject(self, [x, y, depth]: [f32; 3]) -> [f32; 3] {
        let p = self.projection;
        self.unrotate([
            (x - p.offset[0]) / p.scale,
            (p.offset[1] - y) / (p.scale * p.view.aspect),
            depth,
        ])
    }
}
#[derive(Clone, Copy)]
struct Triangle {
    points: [[f32; 3]; 3],
    normal: [f32; 3],
    palette: u8,
    occlusion: u8,
}
#[derive(Default)]
struct Scene {
    triangles: Vec<Triangle>,
}
impl Scene {
    fn quad(&mut self, p: [[f32; 3]; 4], normal: [f32; 3], palette: u8, occlusion: u8) {
        for indices in [[0, 1, 2], [0, 2, 3]] {
            self.triangles.push(Triangle {
                points: indices.map(|i| p[i]),
                normal,
                palette: palette.min(15),
                occlusion,
            });
        }
    }
    fn solid(&mut self, lo: [f32; 3], hi: [f32; 3], palette: u8, contact: u8) {
        let [x, y, z] = lo;
        let [a, b, c] = hi;
        self.quad(
            [[x, b, z], [a, b, z], [a, b, c], [x, b, c]],
            [0., 1., 0.],
            palette,
            contact / 4,
        );
        self.quad(
            [[x, y, c], [a, y, c], [a, y, z], [x, y, z]],
            [0., -1., 0.],
            palette,
            contact,
        );
        self.quad(
            [[x, y, z], [x, b, z], [x, b, c], [x, y, c]],
            [-1., 0., 0.],
            palette,
            contact,
        );
        self.quad(
            [[a, y, c], [a, b, c], [a, b, z], [a, y, z]],
            [1., 0., 0.],
            palette,
            contact,
        );
        self.quad(
            [[a, y, z], [a, b, z], [x, b, z], [x, y, z]],
            [0., 0., -1.],
            palette,
            contact,
        );
        self.quad(
            [[x, y, c], [x, b, c], [a, b, c], [a, y, c]],
            [0., 0., 1.],
            palette,
            contact,
        );
    }
    fn pyramid(&mut self, center: [f32; 3], radius: f32, height: f32, palette: u8) {
        let [x, y, z] = center;
        let base = [
            [x - radius, y, z - radius],
            [x + radius, y, z - radius],
            [x + radius, y, z + radius],
            [x - radius, y, z + radius],
        ];
        for i in 0..4 {
            let points = [base[i], base[(i + 1) % 4], [x, y + height, z]];
            let a = sub(points[1], points[0]);
            let b = sub(points[2], points[0]);
            let n = [
                a[1] * b[2] - a[2] * b[1],
                a[2] * b[0] - a[0] * b[2],
                a[0] * b[1] - a[1] * b[0],
            ];
            let len = (n[0] * n[0] + n[1] * n[1] + n[2] * n[2]).sqrt();
            // Base winding faces inward; reverse its computed side normal.
            let normal = n.map(|v| -v / len);
            self.triangles.push(Triangle {
                points,
                normal,
                palette: palette.min(15),
                occlusion: 16,
            });
        }
        self.quad(base, [0., -1., 0.], palette, 40);
    }
}
fn sub(a: [f32; 3], b: [f32; 3]) -> [f32; 3] {
    [a[0] - b[0], a[1] - b[1], a[2] - b[2]]
}

fn room(
    w: f32,
    d: f32,
    h: f32,
    detail: SpriteStyle,
    habitat: Option<HabitatView<'_>>,
    view: View,
) -> Scene {
    let theme = habitat.and_then(|v| v.rules.theme(v.state.theme));
    let floor = theme.map_or(14, |t| t.floor);
    let wall = theme.map_or(6, |t| t.wall);
    let accent = theme.map_or(2, |t| t.accent);
    let pattern = theme.map_or(0, |t| t.pattern);
    let seed = habitat.map_or(1, |v| v.state.seed);
    let fine = detail == SpriteStyle::SixteenBit;
    let mut scene = Scene::default();
    scene.solid(
        [-0.18, -0.28, -0.18],
        [w + 0.18, -0.06, d + 0.18],
        accent,
        100,
    );
    for x in 0..w as u8 {
        for z in 0..d as u8 {
            let xf = f32::from(x);
            let zf = f32::from(z);
            let gap = if fine { 0.022 } else { 0.035 };
            let color = if pattern != 0
                && seed
                    .wrapping_add(u64::from(x) + u64::from(z))
                    .is_multiple_of(4)
            {
                accent
            } else {
                floor
            };
            scene.solid(
                [xf + gap, -0.065, zf + gap],
                [xf + 1. - gap, 0., zf + 1. - gap],
                color,
                32,
            );
            if fine && pattern == 7 {
                scene.solid(
                    [xf + 0.48, 0.001, zf + gap],
                    [xf + 0.51, 0.008, zf + 1. - gap],
                    accent,
                    20,
                );
            }
        }
    }
    // Outward normals with positive view depth are the near walls, cut away.
    for (side, normal) in [
        (0, [-1., 0., 0.]),
        (1, [1., 0., 0.]),
        (2, [0., 0., -1.]),
        (3, [0., 0., 1.]),
    ] {
        if view.rotate(normal)[2] > 0.00001 {
            continue;
        }
        let length = if side < 2 { d } else { w };
        let door = seed.wrapping_add(u64::from(side)).is_multiple_of(3);
        let center = length * (0.4 + (seed.rotate_right(u32::from(side) * 7) % 20) as f32 / 100.);
        let half = if door { 0.57 } else { 0.75 };
        let a = (center - half).max(0.35);
        let b = (center + half).min(length - 0.35);
        let bottom = if door { 0.0 } else { h * 0.4 };
        let top = if door { h * 0.73 } else { h * 0.79 };
        wall_box(&mut scene, side, w, d, [0., 0., a, h], wall, 52);
        wall_box(&mut scene, side, w, d, [b, 0., length, h], wall, 52);
        if bottom > 0. {
            wall_box(&mut scene, side, w, d, [a, 0., b, bottom], wall, 64);
        }
        wall_box(&mut scene, side, w, d, [a, top, b, h], wall, 24);
        // The opening is real: no wall face lies behind its recessed panel.
        panel(
            &mut scene,
            side,
            w,
            d,
            [a + 0.06, bottom + 0.06, b - 0.06, top - 0.06],
            if door { accent } else { floor },
            0.025,
        );
        wall_box(
            &mut scene,
            side,
            w,
            d,
            [a - 0.055, bottom, b + 0.055, bottom + 0.09],
            accent,
            32,
        );
        wall_box(
            &mut scene,
            side,
            w,
            d,
            [a - 0.055, top - 0.06, b + 0.055, top + 0.055],
            accent,
            20,
        );
        wall_box(
            &mut scene,
            side,
            w,
            d,
            [a - 0.055, bottom, a + 0.055, top],
            accent,
            24,
        );
        wall_box(
            &mut scene,
            side,
            w,
            d,
            [b - 0.055, bottom, b + 0.055, top],
            accent,
            24,
        );
        if !door {
            // A sill projects into the room, carrying its own top/side depth.
            panel(
                &mut scene,
                side,
                w,
                d,
                [a - 0.14, bottom - 0.08, b + 0.14, bottom + 0.04],
                accent,
                0.3,
            );
            if fine {
                wall_box(
                    &mut scene,
                    side,
                    w,
                    d,
                    [center - 0.035, bottom, center + 0.035, top],
                    accent,
                    16,
                );
                wall_box(
                    &mut scene,
                    side,
                    w,
                    d,
                    [
                        a,
                        (bottom + top) * 0.5 - 0.03,
                        b,
                        (bottom + top) * 0.5 + 0.03,
                    ],
                    accent,
                    16,
                );
            }
        } else {
            panel(
                &mut scene,
                side,
                w,
                d,
                [b - 0.22, top * 0.44, b - 0.12, top * 0.44 + 0.1],
                floor,
                0.15,
            );
        }
        wall_box(&mut scene, side, w, d, [0., h, length, h + 0.1], accent, 12);
        wall_box(&mut scene, side, w, d, [0., 0., a, 0.12], accent, 80);
        wall_box(&mut scene, side, w, d, [b, 0., length, 0.12], accent, 80);
    }
    if let Some(v) = habitat {
        let mut occupied = 0u16;
        // Respect placed + owned/earned + one item per legal slot, even for an
        // unvalidated read-only preview. Never infer or grant a purchase.
        for item in v.rules.items.iter().take(64) {
            let Some(bit) = 1u64.checked_shl(u32::from(item.id)) else {
                continue;
            };
            let Some(slot) = 1u16.checked_shl(u32::from(item.slot)) else {
                continue;
            };
            if v.state.placed & bit == 0
                || !v.state.owns(item.id, v.wins, v.age, v.rules)
                || occupied & slot != 0
                || item.kind > 7
            {
                continue;
            }
            occupied |= slot;
            let (nx, nz) = v.state.layout(item.slot);
            let x = 0.7 + f32::from(nx.min(1000)) / 1000. * (w - 1.4);
            let z = 0.7 + f32::from(nz.min(1000)) / 1000. * (d - 1.4);
            furniture(&mut scene, item.kind, [x, 0.025, z], accent, floor, fine);
        }
    }
    scene
}
// Rectangle [along_min, height_min, along_max, height_max] on one room wall.
fn wall_box(scene: &mut Scene, side: u8, w: f32, d: f32, r: [f32; 4], color: u8, contact: u8) {
    let [a, y, b, h] = r;
    let (lo, hi) = match side {
        0 => ([-0.16, y, a], [0., h, b]),
        1 => ([w, y, a], [w + 0.16, h, b]),
        2 => ([a, y, -0.16], [b, h, 0.]),
        _ => ([a, y, d], [b, h, d + 0.16]),
    };
    scene.solid(lo, hi, color, contact);
}
fn panel(scene: &mut Scene, side: u8, w: f32, d: f32, r: [f32; 4], color: u8, inset: f32) {
    let [a, y, b, h] = r;
    let (lo, hi) = match side {
        0 => ([-0.1, y, a], [inset, h, b]),
        1 => ([w - inset, y, a], [w + 0.1, h, b]),
        2 => ([a, y, -0.1], [b, h, inset]),
        _ => ([a, y, d - inset], [b, h, d + 0.1]),
    };
    scene.solid(lo, hi, color, 24);
}
fn furniture(scene: &mut Scene, kind: u8, origin: [f32; 3], accent: u8, floor: u8, fine: bool) {
    let [x, y, z] = origin;
    let mut block = |lo: [f32; 3], hi: [f32; 3], palette, contact| {
        scene.solid(
            [x + lo[0], y + lo[1], z + lo[2]],
            [x + hi[0], y + hi[1], z + hi[2]],
            palette,
            contact,
        );
    };
    match kind {
        0 => {
            // Pot, soil, stem, two pyramidal leaf crowns.
            block([-0.23, 0., -0.23], [0.23, 0.38, 0.23], accent, 96);
            block([-0.26, 0.29, -0.26], [0.26, 0.4, 0.26], floor, 40);
            block([-0.055, 0.38, -0.055], [0.055, 1.15, 0.055], accent, 24);
            scene.pyramid([x, y + 0.58, z], 0.38, 0.48, accent);
            if fine {
                scene.pyramid([x + 0.16, y + 0.85, z + 0.06], 0.27, 0.42, accent);
            }
        }
        1 => {
            // Chair: four legs, seat, back and upholstered inset.
            for dx in [-0.34, 0.24] {
                for dz in [-0.31, 0.24] {
                    block([dx, 0., dz], [dx + 0.1, 0.62, dz + 0.1], accent, 96);
                }
            }
            block([-0.42, 0.52, -0.4], [0.42, 0.7, 0.4], accent, 40);
            block([-0.42, 0.66, -0.42], [0.42, 1.28, -0.26], accent, 32);
            if fine {
                block([-0.3, 0.72, -0.25], [0.3, 1.14, -0.21], floor, 20);
            }
        }
        2 => {
            // Table with a substantial top, apron and four individual legs.
            for dx in [-0.49, 0.36] {
                for dz in [-0.32, 0.19] {
                    block([dx, 0., dz], [dx + 0.13, 0.87, dz + 0.13], accent, 96);
                }
            }
            block([-0.59, 0.78, -0.43], [0.59, 0.94, 0.43], accent, 36);
            if fine {
                block([-0.22, 0.945, -0.17], [0.22, 0.97, 0.17], floor, 20);
            }
        }
        3 => {
            block([-0.27, 0., -0.27], [0.27, 0.12, 0.27], accent, 96);
            block([-0.045, 0.1, -0.045], [0.045, 1.35, 0.045], accent, 24);
            block([-0.37, 1.12, -0.37], [0.37, 1.24, 0.37], floor, 20);
            scene.pyramid([x, y + 1.23, z], 0.37, 0.38, floor);
        }
        4 => {
            // Raised woven rug, with actual border thickness.
            block([-0.68, 0., -0.46], [0.68, 0.035, 0.46], accent, 60);
            block([-0.54, 0.036, -0.32], [0.54, 0.045, 0.32], floor, 24);
            if fine {
                for dx in [-0.4, 0., 0.4] {
                    block(
                        [dx - 0.035, 0.046, -0.28],
                        [dx + 0.035, 0.052, 0.28],
                        accent,
                        12,
                    );
                }
            }
        }
        5 => {
            block([-0.55, 0., -0.27], [-0.43, 1.52, 0.27], accent, 64);
            block([0.43, 0., -0.27], [0.55, 1.52, 0.27], accent, 64);
            block([-0.43, 0.05, -0.27], [0.43, 1.5, -0.19], accent, 64);
            for level in [0.1, 0.72, 1.4] {
                block([-0.55, level, -0.27], [0.55, level + 0.1, 0.27], accent, 48);
                if fine && level < 1. {
                    for dx in [-0.3, -0.05, 0.2] {
                        block(
                            [dx, level + 0.1, -0.12],
                            [dx + 0.16, level + 0.42, 0.14],
                            floor,
                            36,
                        );
                    }
                }
            }
        }
        6 => {
            block([-0.3, 0., -0.3], [0.3, 0.15, 0.3], accent, 96);
            block([-0.15, 0.15, -0.15], [0.15, 0.38, 0.15], floor, 40);
            scene.pyramid([x, y + 0.38, z], 0.3, 0.5, accent);
        }
        7 => {
            block([-0.29, 0., -0.24], [0.29, 0.15, 0.24], accent, 96);
            block([-0.06, 0.15, -0.06], [0.06, 0.58, 0.06], accent, 24);
            block([-0.25, 0.53, -0.18], [0.25, 0.85, 0.18], floor, 20);
            block([-0.36, 0.61, -0.08], [-0.25, 0.8, 0.08], accent, 24);
            block([0.25, 0.61, -0.08], [0.36, 0.8, 0.08], accent, 24);
            if fine {
                block([-0.12, 0.85, -0.12], [0.12, 0.92, 0.12], accent, 12);
            }
        }
        _ => {}
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        game::Game,
        habitat::{Habitat, HabitatRules, Item, Theme},
    };
    fn camera(yaw: u16) -> WorldCamera {
        WorldCamera {
            yaw_degrees: yaw,
            zoom_percent: 100,
            position: (500, 500),
            pixel_aspect: 1.,
            detail: SpriteStyle::SixteenBit,
            pan_permille: (0, 0),
            elevation_radians: None,
        }
    }
    fn fixture() -> HabitatRules {
        HabitatRules {
            themes: vec![Theme {
                id: 0,
                name: "Fixture".into(),
                wall: 6,
                floor: 14,
                accent: 2,
                pattern: 2,
            }],
            items: (0..8)
                .map(|kind| Item {
                    id: kind,
                    name: format!("Item {kind}"),
                    price: 10,
                    kind,
                    slot: kind,
                    reward_wins: 0,
                    reward_age: 0,
                })
                .collect(),
        }
    }
    fn view<'a>(state: &'a Habitat, rules: &'a HabitatRules) -> HabitatView<'a> {
        HabitatView {
            state,
            rules,
            wins: 0,
            age: 0,
        }
    }
    fn blank() -> WorldFrame {
        WorldFrame {
            width: 32,
            height: 32,
            pixels: vec![None; 1024],
            pet_anchor: [0.; 3],
            scale: 1.,
            pet_height_pixels: 1.,
            projection: None,
        }
    }
    fn close3(actual: [f32; 3], expected: [f32; 3]) {
        for (a, e) in actual.into_iter().zip(expected) {
            assert!((a - e).abs() < 0.0001, "{actual:?} != {expected:?}");
        }
    }

    #[test]
    fn surface_metadata_tracks_the_depth_winner_and_coincident_tie() {
        let mut projection = Projection {
            view: View::new(45, 1.),
            scale: 8.,
            offset: [0., 0.],
        };
        let point = [0., 1., 0.];
        let screen = projection.point(point);
        projection.offset = [16.5 - screen[0], 16.5 - screen[1]];
        let floor = Triangle {
            points: [[0., 1., 0.], [1., 1., -1.], [-1., 1., -1.]],
            normal: [0., 1., 0.],
            palette: 3,
            occlusion: 0,
        };
        // The sample is the shared first vertex, so its barycentric weight
        // is exactly one in both triangles. Interior interpolation can differ
        // by an ULP and must not be mistaken for an exact depth tie.
        let front = Triangle {
            points: [[0., 1., 0.], [-1., 0., 0.], [1., 0., 0.]],
            normal: [0., 0., 1.],
            palette: 9,
            occlusion: 0,
        };
        let near = Triangle {
            points: [[-4., 1.5, -4.], [4., 1.5, -4.], [0., 1.5, 4.]],
            ..floor
        };
        for triangles in [[floor, front], [front, floor]] {
            let mut f = blank();
            f.projection = Some(SurfaceProjection::new(projection));
            for t in triangles {
                f.triangle(t, projection);
            }
            assert_eq!(f.sample(16, 16).unwrap().palette, 9);
            let surface = f.sample_surface(16, 16).unwrap();
            close3(surface.position, point);
            close3(surface.normal, front.normal);
            f.triangle(near, projection);
            let surface = f.sample_surface(16, 16).unwrap();
            assert_eq!(f.sample(16, 16).unwrap().palette, 3);
            assert!(surface.position[1] > 1.4);
            close3(surface.normal, floor.normal);
        }
    }

    #[test]
    fn surface_coordinates_remain_model_space_across_camera_transforms() {
        let mut eligible = 0;
        for yaw in [0, 45, 90, 135, 180, 225, 270, 315] {
            for aspect in [0.5, 1., 2.] {
                for scale in [3., 11., 27.] {
                    let mut projection = Projection {
                        view: View::new(yaw, aspect),
                        scale,
                        offset: [0., 0.],
                    };
                    // Arbitrary tilt exercises the same inverse used during camera
                    // transitions. Pixel centres hit the same physical floor point.
                    projection.view.elevation = 48_f32.to_radians();
                    let point = [2.25, 1.25, 3.25];
                    let screen = projection.point(point);
                    projection.offset = [14.5 - screen[0], 17.5 - screen[1]];
                    let t = Triangle {
                        points: [[1., 1.25, 2.], [4., 1.25, 2.], [2.5, 1.25, 5.]],
                        normal: [0., 1., 0.],
                        palette: 3,
                        occlusion: 0,
                    };
                    let mut f = blank();
                    f.projection = Some(SurfaceProjection::new(projection));
                    f.triangle(t, projection);
                    let surface = f.sample_surface(14, 17).expect("non-vacuous surface");
                    close3(surface.position, point);
                    close3(surface.normal, [0., 1., 0.]);
                    assert_eq!(surface.position.map(|v| v.floor() as i32), [2, 1, 3]);
                    eligible += 1;
                }
            }
        }
        assert_eq!(eligible, 72);
    }

    #[test]
    fn lattice_extent_is_the_projected_model_cube_not_view_normal_or_viewport() {
        for yaw in [0, 45, 90, 135, 225, 315] {
            let projection = Projection {
                view: View::new(yaw, 1.7),
                scale: 9.,
                offset: [-35., 20.],
            };
            let mut f = blank();
            f.projection = Some(SurfaceProjection::new(projection));
            let mut lo = [f32::INFINITY; 2];
            let mut hi = [f32::NEG_INFINITY; 2];
            for x in [0., 0.25] {
                for y in [0., 0.25] {
                    for z in [0., 0.25] {
                        let p = projection.point([x, y, z]);
                        for axis in 0..2 {
                            lo[axis] = lo[axis].min(p[axis]);
                            hi[axis] = hi[axis].max(p[axis]);
                        }
                    }
                }
            }
            let extent = f.lattice_extent_pixels(0.25).unwrap();
            for axis in 0..2 {
                assert!((extent[axis] - (hi[axis] - lo[axis])).abs() < 0.0001);
            }
            let double = f.lattice_extent_pixels(0.5).unwrap();
            assert_eq!(double, extent.map(|v| v * 2.));
        }
    }

    #[test]
    fn surface_lattice_edges_choose_the_inside_cell_with_bounded_tolerance() {
        for error in [-0.00005, 0., 0.00005] {
            for normal in [[1., 0., 0.], [-1., 0., 0.], [0., 1., 0.]] {
                let surface = WorldSurface {
                    position: [2. + error, -1. + error, 3.25],
                    normal,
                };
                let expected = if normal[0] > 0. {
                    [1, -1, 3]
                } else if normal[1] > 0. {
                    [2, -2, 3]
                } else {
                    [2, -1, 3]
                };
                assert_eq!(surface.lattice_cell(1.), Some(expected));
                let scaled = WorldSurface {
                    position: surface.position.map(|v| v * 0.25),
                    ..surface
                };
                assert_eq!(scaled.lattice_cell(0.25), Some(expected));
            }
        }
        let mut surface = WorldSurface {
            position: [0.25, -0.25, 1.25],
            normal: [0., 1., 0.],
        };
        assert_eq!(surface.lattice_cell(0.5), Some([0, -1, 2]));
        for pitch in [0., -1., f32::NAN, f32::INFINITY, f32::MIN_POSITIVE] {
            assert_eq!(surface.lattice_cell(pitch), None);
        }
        surface.position[0] = f32::INFINITY;
        assert_eq!(surface.lattice_cell(1.), None);
        surface.position[0] = 0.;
        surface.normal[2] = f32::NAN;
        assert_eq!(surface.lattice_cell(1.), None);
    }

    #[test]
    #[ignore = "explicit release-mode cost measurement, not a timing gate"]
    fn surface_accessor_cost_measurement() {
        use std::{hint::black_box, time::Instant};
        let f = WorldFrame::render(&WorldRules::default(), None, camera(45), (256, 128));
        let visible = f.pixels.iter().flatten().count();
        assert!(visible > 100);
        for trial in 0..3 {
            let start = Instant::now();
            for _ in 0..32 {
                for y in 0..u32::from(f.height) {
                    for x in 0..u32::from(f.width) {
                        black_box(f.sample(black_box(x), black_box(y)));
                    }
                }
            }
            let plain = start.elapsed();
            let start = Instant::now();
            for _ in 0..32 {
                for y in 0..u32::from(f.height) {
                    for x in 0..u32::from(f.width) {
                        black_box(f.sample_surface(black_box(x), black_box(y)));
                    }
                }
            }
            let surface = start.elapsed();
            let samples = 32. * f64::from(f.width) * f64::from(f.height);
            println!("trial={trial} pixels={} visible={visible} plain_ns_per_query={:.2} surface_ns_per_query={:.2} surface_ms_per_frame={:.3}",
                f.pixels.len(), plain.as_nanos() as f64 / samples,
                surface.as_nanos() as f64 / samples, surface.as_secs_f64() * 1000. / 32.);
        }
    }

    #[test]
    fn surface_metadata_empty_disabled_out_of_bounds_and_bad_pitch_are_safe() {
        let rules = WorldRules::default();
        for size in [(0, 0), (0, 20), (20, 0)] {
            let f = WorldFrame::render(&rules, None, camera(45), size);
            assert_eq!(f.sample_surface(0, 0), None);
            assert_eq!(f.lattice_extent_pixels(1.), None);
        }
        let disabled = WorldRules {
            enabled: false,
            ..rules
        };
        let f = WorldFrame::render(&disabled, None, camera(45), (32, 32));
        assert_eq!(f.sample_surface(16, 16), None);
        assert_eq!(f.lattice_extent_pixels(1.), None);
        let f = WorldFrame::render(&rules, None, camera(45), (100, 80));
        assert_eq!(f.sample_surface(u32::MAX, u32::MAX), None);
        assert_eq!(f.sample_surface(100, 0), None);
        assert_eq!(f.sample_surface(0, 80), None);
        for pitch in [
            0.,
            -1.,
            f32::NAN,
            f32::INFINITY,
            f32::NEG_INFINITY,
            f32::MAX,
        ] {
            assert_eq!(f.lattice_extent_pixels(pitch), None);
        }
        let mut visible = 0;
        for y in 0..u32::from(f.height) {
            for x in 0..u32::from(f.width) {
                assert_eq!(f.sample_surface(x, y).is_some(), f.sample(x, y).is_some());
                visible += usize::from(f.sample_surface(x, y).is_some());
            }
        }
        assert!(visible > 100, "metadata must not pass on an empty frame");
    }

    #[test]
    fn rules_reject_unknown_and_invalid_values() {
        assert!(WorldRules::default().validate().is_ok());
        for i in 0..10 {
            let mut r = WorldRules::default();
            match i {
                0 => r.width_tiles = 0,
                1 => r.depth_tiles = 255,
                2 => r.wall_height = 255,
                3 => r.pet_height_percent = 0,
                4 => r.default_yaw_degrees = 360,
                5 => r.orbit_step_degrees = 0,
                6 => r.elevation_degrees = 31,
                7 => r.follow_percent = 101,
                8 => r.backdrop = 16,
                _ => r.orbit_step_degrees = 181,
            }
            assert!(r.validate().is_err(), "{i}");
        }
        let serialized = "enabled=true\nwidth_tiles=8\ndepth_tiles=8\nwall_height=4\npet_height_percent=65\ndefault_yaw_degrees=45\norbit_step_degrees=45\nelevation_degrees=30\nfollow_percent=100\nbackdrop=6\nextra=1";
        assert!(toml::from_str::<WorldRules>(serialized).is_err());
    }
    #[test]
    fn quarter_turns_rotate_floor_walls_furniture_and_wrap_exactly() {
        let rules = WorldRules::default();
        let catalogue = fixture();
        let state = Habitat {
            seed: 37,
            theme: 0,
            owned: 255,
            placed: 255,
        };
        let mut frames = Vec::new();
        for yaw in [0, 90, 180, 270, 360] {
            frames.push(WorldFrame::render(
                &rules,
                Some(view(&state, &catalogue)),
                camera(yaw),
                (192, 128),
            ));
        }
        assert_eq!(frames[0].pixels, frames[4].pixels);
        assert_eq!(frames[0].pet_anchor, frames[4].pet_anchor);
        for pair in frames[..4].windows(2) {
            assert_ne!(pair[0].pixels, pair[1].pixels);
        }
        // Isolate each class, so a rotating wall cannot conceal static furniture/floor.
        for kind in [2, 4] {
            let mut a = Scene::default();
            furniture(&mut a, kind, [2., 0., 3.], 2, 14, true);
            let project = |yaw| Projection {
                view: View::new(yaw, 1.),
                scale: 10.,
                offset: [16., 16.],
            };
            let mut f = blank();
            let mut g = blank();
            for t in a.triangles {
                f.triangle(t, project(0));
                g.triangle(t, project(90));
            }
            assert_ne!(f.pixels, g.pixels);
        }
        let a = room(8., 8., 4., SpriteStyle::EightBit, None, View::new(45, 1.));
        let b = room(8., 8., 4., SpriteStyle::EightBit, None, View::new(135, 1.));
        assert_ne!(
            a.triangles
                .iter()
                .filter(|t| t.normal == [1., 0., 0.])
                .count(),
            b.triangles
                .iter()
                .filter(|t| t.normal == [1., 0., 0.])
                .count()
        );
    }
    #[test]
    fn immutable_seeded_rendering_never_consumes_game_rng() {
        let config = crate::config::Config::sample().unwrap();
        let game = Game::new(&config.game, 42);
        let before = game.clone();
        let rules = WorldRules::default();
        let habitat = HabitatView {
            state: &game.habitat,
            rules: &config.habitat,
            wins: 0,
            age: 0,
        };
        let a = WorldFrame::render(&rules, Some(habitat), camera(37), (120, 80));
        let b = WorldFrame::render(&rules, Some(habitat), camera(37), (120, 80));
        assert_eq!(a.pixels, b.pixels);
        assert_eq!(game, before);
        let mut different = game.habitat;
        different.seed ^= 0x1248;
        let c = WorldFrame::render(
            &rules,
            Some(view(&different, &config.habitat)),
            camera(37),
            (120, 80),
        );
        assert_ne!(a.pixels, c.pixels);
    }
    #[test]
    fn full_width_birth_seeds_cannot_overflow_room_patterns() {
        let rules = WorldRules::default();
        let habitat_rules = fixture();
        for seed in [0, 1, u64::MAX - 2, u64::MAX - 1, u64::MAX] {
            let state = Habitat {
                seed,
                theme: 0,
                owned: 0,
                placed: 0,
            };
            let frame = WorldFrame::render(
                &rules,
                Some(view(&state, &habitat_rules)),
                camera(45),
                (80, 50),
            );
            assert!(frame.pixels.iter().any(Option::is_some));
        }
    }
    #[test]
    fn depth_buffer_retains_nearer_material_normal_and_contact_in_any_order() {
        let mut scene = Scene::default();
        scene.solid([-0.9, 0., -0.2], [0.9, 1.5, 0.2], 3, 80);
        scene.solid([-0.7, 0., 0.5], [0.7, 1.5, 0.7], 9, 24);
        let projection = Projection {
            view: View::new(0, 1.),
            scale: 10.,
            offset: [16., 24.],
        };
        let mut a = blank();
        let mut b = blank();
        for &t in &scene.triangles {
            a.triangle(t, projection);
        }
        for &t in scene.triangles.iter().rev() {
            b.triangle(t, projection);
        }
        assert_eq!(a.pixels, b.pixels);
        let sample = a.sample(16, 18).unwrap();
        assert_eq!(sample.palette, 9);
        assert_eq!(sample.occlusion, 24);
        let normal = projection.view.rotate([0., 0., 1.]);
        assert_eq!(sample.normal, normal);
        // Recover world Y from screen Y and the known front face Z.
        let y = ((24. - 18.5) / 10. + 0.5 * 0.7) / 30_f32.to_radians().cos();
        let depth = 0.5 * y + 30_f32.to_radians().cos() * 0.7;
        assert!((sample.depth - depth).abs() < 0.00001);
    }
    #[test]
    fn absent_removed_unowned_and_unearned_decorations_produce_no_geometry() {
        let mut rules = fixture();
        rules.items[7].price = 0;
        rules.items[7].reward_wins = 3;
        rules.items[7].reward_age = 10;
        let mut state = Habitat {
            seed: 29,
            theme: 0,
            owned: 0,
            placed: 0,
        };
        fn triangles(v: HabitatView<'_>) -> usize {
            room(
                8.,
                8.,
                4.,
                SpriteStyle::SixteenBit,
                Some(v),
                View::new(45, 1.),
            )
            .triangles
            .len()
        }
        let base = triangles(view(&state, &rules));
        state.placed = 255;
        assert_eq!(triangles(view(&state, &rules)), base);
        state.owned = 127;
        assert!(triangles(view(&state, &rules)) > base);
        for kind in 0..8 {
            state.owned = 1 << kind;
            state.placed = 1 << kind;
            let mut v = view(&state, &rules);
            v.wins = 3;
            v.age = 10;
            assert!(triangles(v) > base, "kind {kind}");
            v.age = 0;
            v.wins = 0;
            if kind == 7 {
                assert_eq!(triangles(v), base);
            }
        }
        state.placed = 0;
        assert_eq!(triangles(view(&state, &rules)), base);
    }
    #[test]
    fn coherent_follow_and_offset_use_the_room_projection() {
        let rules = WorldRules::default();
        for yaw in [0, 45, 123, 270] {
            let mut c = camera(yaw);
            c.position = (u16::MAX, 0);
            let f = WorldFrame::render(&rules, None, c, (100, 80));
            assert!((f.pet_anchor[0] - 50.).abs() < 0.0001);
            assert!((f.pet_anchor[1] - 49.6).abs() < 0.0001);
            let expected = View::new(yaw, 1.).rotate([7.35, 0.035, 0.65]);
            assert!((f.pet_anchor[2] - expected[2]).abs() < 0.0001);
        }
        let mut c = camera(45);
        c.pixel_aspect = f32::NAN;
        let f = WorldFrame::render(&rules, None, c, (100, 80));
        assert!(f.pet_anchor.iter().all(|v| v.is_finite()));
    }
    #[test]
    fn empty_tiny_extreme_sizes_zoom_and_normals_are_bounded() {
        let rules = WorldRules::default();
        for size in [
            (0, 0),
            (0, 100),
            (100, 0),
            (1, 1),
            (2, 19),
            (80, 12),
            (12, 80),
            (u16::MAX, 1),
        ] {
            for zoom in [0, 25, 800, u16::MAX] {
                let mut c = camera(u16::MAX);
                c.zoom_percent = zoom;
                c.position = (u16::MAX, u16::MAX);
                let f = WorldFrame::render(&rules, None, c, size);
                assert_eq!(f.pixels.len(), usize::from(f.width) * usize::from(f.height));
                assert!(f.pixels.len() <= 1024 * 1024);
                assert_eq!(f.sample(u32::MAX, u32::MAX), None);
                for p in f.pixels.iter().flatten() {
                    let n = p.normal.iter().map(|v| v * v).sum::<f32>();
                    assert!((n - 1.).abs() < 0.00001);
                    assert!(p.depth.is_finite());
                    assert!(p.palette <= 15);
                }
            }
        }
        let mut c = camera(0);
        c.zoom_percent = u16::MAX;
        let f = WorldFrame::render(&rules, None, c, (u16::MAX, u16::MAX));
        assert_eq!((f.width, f.height), (1024, 1024));
        let disabled = WorldRules {
            enabled: false,
            ..rules
        };
        assert!(WorldFrame::render(&disabled, None, c, (10, 10))
            .pixels
            .iter()
            .all(Option::is_none));
    }
}

#[cfg(test)]
mod camera_pose_regressions {
    use super::*;
    #[test]
    fn actual_room_pan_tilt_and_reset_preserve_default_geometry() {
        let rules = crate::config::Config::sample().unwrap().world;
        let camera = WorldCamera {
            yaw_degrees: 45,
            zoom_percent: 100,
            position: (500, 500),
            pixel_aspect: 1.0,
            detail: SpriteStyle::SixteenBit,
            pan_permille: (0, 0),
            elevation_radians: None,
        };
        let base = WorldFrame::render(&rules, None, camera, (120, 80));
        let same = WorldFrame::render(
            &rules,
            None,
            WorldCamera {
                elevation_radians: Some(30_f32.to_radians()),
                ..camera
            },
            (120, 80),
        );
        assert_eq!(base.pixels, same.pixels);
        assert_eq!(base.pet_anchor, same.pet_anchor);
        let pan = WorldFrame::render(
            &rules,
            None,
            WorldCamera {
                pan_permille: (100, -50),
                ..camera
            },
            (120, 80),
        );
        assert_ne!(base.pixels, pan.pixels);
        assert!((pan.pet_anchor[0] - base.pet_anchor[0] + 12.0).abs() < 0.001);
        let tilt = WorldFrame::render(
            &rules,
            None,
            WorldCamera {
                elevation_radians: Some(42_f32.to_radians()),
                ..camera
            },
            (120, 80),
        );
        assert_ne!(base.pixels, tilt.pixels);
        assert_ne!(base.pet_height_pixels, tilt.pet_height_pixels);
        let reset = WorldFrame::render(&rules, None, camera, (120, 80));
        assert_eq!(base.pixels, reset.pixels);
    }
}
