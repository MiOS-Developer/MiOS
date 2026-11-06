// Temporary audit diagnostics for the ASCII voxel overlay. Never committed to a lane branch.
use super::*;
use crate::{
    game::{Game, Stage},
    sprite::{Appearance, Expression, SpriteStyle},
    world::{WorldCamera, WorldFrame},
};
use std::fmt::Write as _;

fn coverage(symbol: &str) -> f64 {
    let Some(c) = symbol.chars().next() else {
        return 0.0;
    };
    match c {
        ' ' => 0.0,
        '█' => 1.0,
        '.' => 0.06,
        ':' => 0.12,
        '-' => 0.12,
        '=' => 0.22,
        '+' => 0.20,
        '*' => 0.25,
        '#' => 0.45,
        '%' => 0.38,
        '@' => 0.50,
        _ => 0.30,
    }
}

fn rgb(color: Color) -> Option<[f64; 3]> {
    terminal_rgb(color).map(|c| c.map(f64::from))
}

fn luma(c: [f64; 3]) -> f64 {
    0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]
}

fn mix(a: [f64; 3], b: [f64; 3], t: f64) -> [f64; 3] {
    [
        a[0] + (b[0] - a[0]) * t,
        a[1] + (b[1] - a[1]) * t,
        a[2] + (b[2] - a[2]) * t,
    ]
}

/// The plane colour packed sample `i` shows, and the perceived colour after
/// glyph/dot coverage (None for terminal-default colours).
fn shown(renderer: Renderer, cell: &ratatui::buffer::Cell, i: usize) -> (Color, Option<[f64; 3]>) {
    let c = cell.symbol().chars().next().unwrap_or(' ');
    let glyph = |cell: &ratatui::buffer::Cell| {
        let eff = rgb(cell.bg)
            .zip(rgb(cell.fg))
            .map(|(bg, fg)| mix(bg, fg, coverage(cell.symbol())));
        (cell.fg, eff)
    };
    let plane = |color: Color| (color, rgb(color));
    match renderer {
        Renderer::Ascii | Renderer::Auto => {
            if c == ' ' {
                plane(cell.bg)
            } else {
                glyph(cell)
            }
        }
        Renderer::HalfBlock => match c {
            '█' => plane(cell.fg),
            '▀' => plane(if i == 0 { cell.fg } else { cell.bg }),
            '▄' => plane(if i == 1 { cell.fg } else { cell.bg }),
            ' ' => plane(cell.bg),
            _ => glyph(cell),
        },
        Renderer::Quadrant => {
            if let Some(mask) = ratatui::symbols::pixel::QUADRANTS
                .iter()
                .position(|q| *q == c)
            {
                plane(if mask & (1 << i) != 0 {
                    cell.fg
                } else {
                    cell.bg
                })
            } else if c == ' ' {
                plane(cell.bg)
            } else {
                glyph(cell)
            }
        }
        Renderer::Braille => {
            let code = c as u32;
            if (0x2800..=0x28FF).contains(&code) {
                let bit = [0, 3, 1, 4, 2, 5, 6, 7][i];
                if (code - 0x2800) & (1 << bit) != 0 {
                    let eff = rgb(cell.bg)
                        .zip(rgb(cell.fg))
                        .map(|(bg, fg)| mix(bg, fg, 0.35));
                    (cell.fg, eff)
                } else {
                    plane(cell.bg)
                }
            } else if c == ' ' {
                plane(cell.bg)
            } else {
                glyph(cell)
            }
        }
    }
}

/// What a monochrome viewer can tell apart: the glyph and whether sample `i`
/// is a lit sub-pixel of it.
fn shape(renderer: Renderer, cell: &ratatui::buffer::Cell, i: usize) -> (char, bool) {
    let c = cell.symbol().chars().next().unwrap_or(' ');
    let on = match renderer {
        Renderer::Ascii | Renderer::Auto => c != ' ',
        Renderer::HalfBlock => match c {
            '█' => true,
            '▀' => i == 0,
            '▄' => i == 1,
            ' ' => false,
            _ => true,
        },
        Renderer::Quadrant => ratatui::symbols::pixel::QUADRANTS
            .iter()
            .position(|q| *q == c)
            .map_or(c != ' ', |mask| mask & (1 << i) != 0),
        Renderer::Braille => {
            let code = c as u32;
            if (0x2800..=0x28FF).contains(&code) {
                (code - 0x2800) & (1 << [0, 3, 1, 4, 2, 5, 6, 7][i]) != 0
            } else {
                c != ' '
            }
        }
    };
    (c, on)
}

fn style_for(renderer: Renderer, w: u16, h: u16, zoom: u16) -> SpriteStyle {
    let (dx, dy) = renderer.packing();
    let z = u64::from(zoom);
    if u64::from(w) * u64::from(dx) * z / 100 >= 64 && u64::from(h) * u64::from(dy) * z / 100 >= 48
    {
        SpriteStyle::SixteenBit
    } else {
        SpriteStyle::EightBit
    }
}

fn appearance(config: &Config, game: &Game) -> Appearance {
    let base = crate::characters::character(&config.characters, game.character);
    let c = crate::genetics::Profile::for_game(game, config)
        .map(|p| p.character(base, config))
        .unwrap_or_else(|| base.clone());
    Appearance {
        shape: c.shape,
        body: c.body,
        accent: c.accent,
    }
}

#[derive(Default)]
struct Tally {
    body: u32,
    sil: u32,
    pairs: u32,
    pairs_luma: u32,
    sep_plane: u32,
    sep_eff: u32,
    edge_fill_n: u32,
    edge_fill_sum: f64,
    sil_rank_n: u32,
    sil_rank_sum: f64,
    int_rank_n: u32,
    int_rank_sum: f64,
    mono_dense: u32,
    interior: u32,
    textured: u32,
    shaded: u32,
    shaded_textured: u32,
    crease: [u32; 3],
    crease_hidden: [u32; 3],
    face_cells: u32,
    shape_distinct: u32,
    core_cells: u32,
    face_scene_bg: u32,
}

#[allow(clippy::too_many_arguments)]
fn measure(
    config: &Config,
    game: &Game,
    look: Appearance,
    stage: Stage,
    renderer: Renderer,
    depth: Depth,
    view: CameraView,
    size: (u16, u16),
    zoom: u16,
) -> Option<String> {
    let (w, h) = size;
    let area = Rect::new(0, 0, w, h);
    let style = style_for(renderer, w, h, zoom);
    let colors = Colors::from_config(config, depth).unwrap();
    let lighting = colors.lighting.clone().unwrap();
    let separation = f64::from(outline_separation(&lighting));
    let camera = Camera {
        orbit_degrees: 45,
        view,
        from_view: view,
        zoom_percent: zoom,
        transition_percent: 100,
    };
    let position = (500, 700);
    let world = view == CameraView::Room;
    let yaw = if view == CameraView::Face { 0 } else { 45 };
    let sprite = if world {
        Sprite::voxel_with_camera(
            stage,
            Expression::Happy,
            style,
            0,
            [45, config.world.elevation_degrees],
            false,
            look,
        )
    } else {
        Sprite::voxel_with_appearance(stage, Expression::Happy, style, 0, yaw, false, look)
    };
    let habitat = HabitatView {
        state: &game.habitat,
        rules: &config.habitat,
        wins: game.wins,
        age: game.age_ticks,
    };
    let (dx, dy) = renderer.packing();
    let aspect = camera_pixel_aspect(renderer);
    let bounds = SolidBounds::of(&sprite)?;
    let (viewport, projection, frame) = if world {
        let caption = u16::from(area.height >= 4);
        let viewport = Rect::new(
            area.x,
            area.y,
            area.width,
            area.height.saturating_sub(caption),
        );
        let frame = WorldFrame::render(
            &config.world,
            Some(habitat),
            WorldCamera {
                yaw_degrees: 45,
                zoom_percent: zoom,
                position,
                pixel_aspect: aspect as f32,
                detail: style,
            },
            (
                viewport.width.saturating_mul(dx),
                viewport.height.saturating_mul(dy),
            ),
        );
        let scale_y = (f64::from(frame.pet_height_pixels) / bounds.height()).max(0.00001);
        let scale = scale_y / aspect;
        let ground = sprite
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
        (viewport, projection, Some(frame))
    } else {
        let caption_rows = u16::from(area.height >= 4);
        let reaction_rows = u16::from(area.height >= 6);
        let margin = u16::from(area.width >= 12);
        let viewport = Rect::new(
            area.x + margin,
            area.y + reaction_rows,
            area.width.saturating_sub(2 * margin),
            area.height.saturating_sub(caption_rows + reaction_rows),
        );
        let projection =
            Projection::camera(&sprite, bounds, viewport, renderer, 0, position, camera);
        (viewport, projection, None)
    };
    let mut buf = Buffer::empty(area);
    RoomWidget {
        world: Some(&config.world),
        sprite: &sprite,
        detail: style,
        renderer,
        scale: 0,
        colors: &colors,
        expression: Expression::Happy,
        frame: 0,
        position,
        camera,
        habitat: Some(habitat),
    }
    .render(area, &mut buf);

    let (dxu, dyu) = (u32::from(dx), u32::from(dy));
    let vw = u32::from(viewport.width) * dxu;
    let vh = u32::from(viewport.height) * dyu;
    let x0 = u32::from(viewport.x) * dxu;
    let y0 = u32::from(viewport.y) * dyu;
    let own = |px: u32, py: u32| -> Option<(u32, u32)> {
        let (sx, sy) = projection.source_pixel(&sprite, px, py)?;
        sprite.pixel(sx, sy, 1)?;
        if let Some(frame) = &frame {
            let sample = frame.sample(px.saturating_sub(x0), py.saturating_sub(y0));
            let depth = f64::from(frame.pet_anchor[2])
                + sprite.depth_pixels(sx, sy).unwrap_or(0.0) * projection.scale
                    / f64::from(frame.scale.max(0.00001));
            if sample.is_some_and(|w| f64::from(w.depth) > depth + 0.0001) {
                return None;
            }
        }
        Some((sx, sy))
    };
    let mut owner = vec![None; (vw * vh) as usize];
    for ly in 0..vh {
        for lx in 0..vw {
            owner[(ly * vw + lx) as usize] = own(x0 + lx, y0 + ly);
        }
    }
    let get = |lx: i64, ly: i64| -> Option<Option<(u32, u32)>> {
        if lx < 0 || ly < 0 || lx >= i64::from(vw) || ly >= i64::from(vh) {
            None
        } else {
            Some(owner[(ly as u32 * vw + lx as u32) as usize])
        }
    };
    let cell_of =
        |lx: u32, ly: u32| -> (u16, u16) { ((x0 + lx) as u16 / dx, (y0 + ly) as u16 / dy) };
    let index_of = |lx: u32, ly: u32| -> usize { ((ly % dyu) * dxu + (lx % dxu)) as usize };
    let cols = usize::from(viewport.width);
    let rows = usize::from(viewport.height);
    let cell_index = |cell: (u16, u16)| -> usize {
        usize::from(cell.1 - viewport.y) * cols + usize::from(cell.0 - viewport.x)
    };
    let mut owned = vec![0u32; cols * rows];
    let mut head = vec![false; cols * rows];
    let mut sil = vec![false; cols * rows];
    let mut rim = vec![(0.0f64, 0u32); cols * rows];
    let mut t = Tally::default();
    for ly in 0..vh {
        for lx in 0..vw {
            let Some((sx, sy)) = owner[(ly * vw + lx) as usize] else {
                continue;
            };
            let cell = cell_of(lx, ly);
            let ci = cell_index(cell);
            owned[ci] += 1;
            if sprite.head_uv(sx, sy).is_some() || sprite.face_uv(sx, sy).is_some() {
                head[ci] = true;
            }
            let here = shown(renderer, &buf[cell], index_of(lx, ly));
            if let Some(eff) = here.1 {
                rim[ci].0 += luma(eff);
                rim[ci].1 += 1;
            }
            for (nx, ny) in [
                (i64::from(lx) - 1, i64::from(ly)),
                (i64::from(lx) + 1, i64::from(ly)),
                (i64::from(lx), i64::from(ly) - 1),
                (i64::from(lx), i64::from(ly) + 1),
            ] {
                if get(nx, ny) != Some(None) {
                    continue;
                }
                sil[ci] = true;
                t.pairs += 1;
                let (nx, ny) = (nx as u32, ny as u32);
                let there = shown(renderer, &buf[cell_of(nx, ny)], index_of(nx, ny));
                if shape(renderer, &buf[cell], index_of(lx, ly))
                    != shape(renderer, &buf[cell_of(nx, ny)], index_of(nx, ny))
                {
                    t.shape_distinct += 1;
                }
                if let (Some(a), Some(b)) = (rgb(here.0), rgb(there.0)) {
                    t.pairs_luma += 1;
                    if (luma(a) - luma(b)).abs() >= separation {
                        t.sep_plane += 1;
                    }
                    if let (Some(a), Some(b)) = (here.1, there.1) {
                        if (luma(a) - luma(b)).abs() >= separation {
                            t.sep_eff += 1;
                        }
                    }
                }
            }
            // Creases toward the right and lower neighbours.
            for (nx, ny) in [(lx + 1, ly), (lx, ly + 1)] {
                let Some(Some((tx, ty))) = get(i64::from(nx), i64::from(ny)) else {
                    continue;
                };
                if (tx, ty) == (sx, sy) {
                    continue;
                }
                let distance = f64::from(tx.abs_diff(sx).max(ty.abs_diff(sy)));
                let da = sprite.depth_pixels(sx, sy).unwrap_or(0.0);
                let db = sprite.depth_pixels(tx, ty).unwrap_or(0.0);
                let na = sprite.surface_shading(sx, sy).map(|s| s.0);
                let nb = sprite.surface_shading(tx, ty).map(|s| s.0);
                let class = if (da - db).abs() > 1.5 * distance + 1.0 {
                    0
                } else if na
                    .zip(nb)
                    .is_some_and(|(a, b)| a[0] * b[0] + a[1] * b[1] + a[2] * b[2] < 0.9)
                {
                    1
                } else if sprite.pixel(sx, sy, 1) != sprite.pixel(tx, ty, 1) {
                    2
                } else {
                    continue;
                };
                t.crease[class] += 1;
                let other_cell = cell_of(nx, ny);
                let there = shown(renderer, &buf[other_cell], index_of(nx, ny));
                let glyph_differs =
                    other_cell != cell && buf[other_cell].symbol() != buf[cell].symbol();
                let colour_differs = match (here.1, there.1) {
                    (Some(a), Some(b)) => (luma(a) - luma(b)).abs() >= 3.0,
                    _ => false,
                };
                if !glyph_differs && !colour_differs {
                    t.crease_hidden[class] += 1;
                }
            }
        }
    }
    let ramp: Vec<char> = lighting.ascii_ramp.chars().collect();
    let rank = |symbol: &str| {
        symbol
            .chars()
            .next()
            .and_then(|c| ramp.iter().position(|r| *r == c))
    };
    let dense = lighting.glyph(180).to_string();
    let full = dxu * dyu;
    for row in 0..rows {
        for col in 0..cols {
            let ci = row * cols + col;
            if owned[ci] == 0 {
                continue;
            }
            t.body += 1;
            let cell = (viewport.x + col as u16, viewport.y + row as u16);
            let symbol = buf[cell].symbol().to_string();
            if sil[ci] {
                t.sil += 1;
                if symbol == dense {
                    t.mono_dense += 1;
                }
                if !head[ci] {
                    if let Some(r) = rank(&symbol) {
                        t.sil_rank_n += 1;
                        t.sil_rank_sum += r as f64;
                    }
                    // Rim versus the nearest interior fill.
                    for (nc, nr) in [
                        (col as i64 - 1, row as i64),
                        (col as i64 + 1, row as i64),
                        (col as i64, row as i64 - 1),
                        (col as i64, row as i64 + 1),
                    ] {
                        if nc < 0 || nr < 0 || nc >= cols as i64 || nr >= rows as i64 {
                            continue;
                        }
                        let ni = nr as usize * cols + nc as usize;
                        if owned[ni] == full
                            && !sil[ni]
                            && !head[ni]
                            && rim[ci].1 > 0
                            && rim[ni].1 > 0
                        {
                            let a = rim[ci].0 / f64::from(rim[ci].1);
                            let b = rim[ni].0 / f64::from(rim[ni].1);
                            t.edge_fill_n += 1;
                            t.edge_fill_sum += (a - b).abs();
                            break;
                        }
                    }
                }
                continue;
            }
            if owned[ci] != full || head[ci] {
                continue;
            }
            t.interior += 1;
            if let Some(r) = rank(&symbol) {
                t.int_rank_n += 1;
                t.int_rank_sum += r as f64;
            }
            let textured = symbol.chars().next().is_some_and(|c| c.is_ascii_graphic());
            t.textured += u32::from(textured);
            let px = u32::from(cell.0) * dxu;
            let py = u32::from(cell.1) * dyu;
            if let Some(light) = projected_surface_light(&sprite, projection, px, py) {
                let value = lighting.brightness(light.normal, light.occlusion, light.edge);
                if 100u16.saturating_sub(value) > lighting.outline_percent.min(99) {
                    t.shaded += 1;
                    t.shaded_textured += u32::from(textured);
                }
            }
        }
    }
    let mut before = Buffer::empty(area);
    draw_projection(
        &sprite,
        renderer,
        &colors,
        projection,
        viewport,
        &mut before,
    );
    let mut after = before.clone();
    draw_camera_face(&sprite, renderer, &colors, projection, viewport, &mut after);
    t.face_cells = before
        .content
        .iter()
        .zip(&after.content)
        .filter(|(a, b)| a != b)
        .count() as u32;
    t.face_scene_bg = before
        .content
        .iter()
        .zip(&after.content)
        .filter(|(a, b)| {
            a != b
                && b.bg == Color::Reset
                && b.symbol()
                    .chars()
                    .next()
                    .is_some_and(|c| c.is_ascii_graphic())
        })
        .count() as u32;
    let mut core = before.clone();
    draw_surface_glyphs(&sprite, renderer, &colors, projection, viewport, &mut core);
    t.core_cells = before
        .content
        .iter()
        .zip(&core.content)
        .filter(|(a, b)| a != b)
        .count() as u32;
    let head_cols = head_surface_bounds(&sprite).map_or(0.0, |hb| {
        let (l, _) = projection.cell(hb.left, hb.top, renderer);
        let (r, _) = projection.cell(hb.right, hb.bottom, renderer);
        r - l
    });
    let tier = FaceTexture::new(&sprite)
        .and_then(|texture| {
            let face_bounds = FaceTexture::bounds(&sprite)?;
            let (l, top) = projection.cell(face_bounds.left, face_bounds.top, renderer);
            let (r, bottom) = projection.cell(face_bounds.right, face_bounds.bottom, renderer);
            let stroke = face_ink(&sprite, &colors, InkRegion::TextBox);
            let pixel_atlas = renderer.packing() != (1, 1)
                && !(renderer == Renderer::HalfBlock && stroke != colors.ink(0));
            Some(format!(
                "{:?}",
                face_lod(texture, r - l, bottom - top, pixel_atlas)
            ))
        })
        .unwrap_or_else(|| "-".into());
    let frac = |a: u32, b: u32| {
        if b == 0 {
            "-".to_string()
        } else {
            format!("{:.3}", f64::from(a) / f64::from(b))
        }
    };
    let mean = |s: f64, n: u32| {
        if n == 0 {
            "-".to_string()
        } else {
            format!("{:.2}", s / f64::from(n))
        }
    };
    Some(format!(
        "{stage:?}\t{style:?}\t{renderer:?}\t{depth:?}\t{view:?}\t{w}x{h}\t{zoom}\t{:.2}\t{:.2}\t{tier}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{:.1}\t{}\t{}",
        projection.scale / f64::from(dx),
        projection.scale_y / f64::from(dy),
        t.body,
        t.sil,
        t.pairs,
        frac(t.sep_plane, t.pairs_luma),
        frac(t.sep_eff, t.pairs_luma),
        mean(t.edge_fill_sum, t.edge_fill_n),
        mean(t.sil_rank_sum, t.sil_rank_n),
        mean(t.int_rank_sum, t.int_rank_n),
        frac(t.mono_dense, t.sil),
        t.interior,
        frac(t.textured, t.interior),
        t.shaded,
        frac(t.shaded_textured, t.shaded),
        t.crease[0],
        frac(t.crease_hidden[0], t.crease[0]),
        t.crease[1],
        frac(t.crease_hidden[1], t.crease[1]),
        t.crease[2],
        frac(t.crease_hidden[2], t.crease[2]),
        t.face_cells,
        u8::from(t.face_cells >= 3),
        frac(t.shape_distinct, t.pairs),
        head_cols,
        t.core_cells,
        t.face_scene_bg,
    ))
}

#[test]
fn ascii_overlay_census() {
    let config = Config::sample().unwrap();
    let mut out = String::from(
        "seed\tstage\tstyle\trenderer\tdepth\tview\tsize\tzoom\tcells_per_px_x\tcells_per_px_y\tface_tier\tbody_cells\tsil_cells\tsil_pairs\tsep_plane\tsep_eff\tedge_fill_dluma\tsil_rank\tint_rank\tmono_dense\tinterior\ttextured\tshaded_interior\tshaded_textured\tinner_pairs\tinner_hidden\tcrease_pairs\tcrease_hidden\tmaterial_pairs\tmaterial_hidden\tface_cells\tface_ok\tshape_distinct\thead_cols\tcore_cells\tface_scene_bg\n",
    );
    let renderers = [
        Renderer::Ascii,
        Renderer::HalfBlock,
        Renderer::Quadrant,
        Renderer::Braille,
    ];
    let sizes = [(22, 5), (38, 9), (47, 22), (71, 20)];
    let views = [CameraView::Room, CameraView::Pet, CameraView::Face];
    for seed in [513u64, 514] {
        let mut game = Game::new(&config.game, seed);
        game.prepare_progress(&config.game).unwrap();
        let look = appearance(&config, &game);
        let _ = writeln!(out, "# seed {seed} appearance {look:?}");
        for (stage, depths) in [
            (Stage::Baby, &[Depth::Indexed, Depth::Mono, Depth::Ansi][..]),
            (Stage::Adult, &[Depth::Indexed][..]),
        ] {
            for &depth in depths {
                for renderer in renderers {
                    for size in sizes {
                        for zoom in [50, 100, 200, 400] {
                            for view in views {
                                if let Some(line) = measure(
                                    &config, &game, look, stage, renderer, depth, view, size, zoom,
                                ) {
                                    let _ = writeln!(out, "{seed}\t{line}");
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    let path = std::path::Path::new("/mios-proof/ascii-overlay-diag.tsv");
    if std::fs::write(path, &out).is_err() {
        println!("{out}");
    }
}

/// Reproduce root's live Face close-up: fox Baby 16-bit, frontal, room panel inner 77x9.
#[test]
fn live_face_layout() {
    let config = Config::sample().unwrap();
    let mut game = Game::new(&config.game, 513);
    game.prepare_progress(&config.game).unwrap();
    let look = appearance(&config, &game);
    let mut out = String::new();
    let whole = Rect::new(0, 0, 130, 28);
    let area = Rect::new(2, 5, 77, 9);
    let viewport = Rect::new(area.x + 1, area.y + 1, area.width - 2, area.height - 2);
    for renderer in [
        Renderer::HalfBlock,
        Renderer::Ascii,
        Renderer::Quadrant,
        Renderer::Braille,
    ] {
        for depth in [Depth::Rgb, Depth::Indexed, Depth::Mono] {
            let colors = Colors::from_config(&config, depth).unwrap();
            for zoom in [100u16, 150, 200, 250, 300, 350, 400] {
                for frame in [0usize, 4, 6] {
                    let sprite = Sprite::voxel_with_appearance(
                        Stage::Baby,
                        Expression::Idle,
                        SpriteStyle::SixteenBit,
                        frame,
                        0,
                        false,
                        look,
                    );
                    let camera = Camera {
                        orbit_degrees: 45,
                        view: CameraView::Face,
                        from_view: CameraView::Face,
                        zoom_percent: zoom,
                        transition_percent: 100,
                    };
                    let bounds = SolidBounds::of(&sprite).unwrap();
                    let projection = Projection::camera(
                        &sprite,
                        bounds,
                        viewport,
                        renderer,
                        0,
                        (500, 700),
                        camera,
                    );
                    let (dx, dy) = renderer.packing();
                    let text = sprite.face_text().unwrap_or("-");
                    let cellr = |b: SolidBounds| {
                        let (l, t) = projection.cell(b.left, b.top, renderer);
                        let (r, bt) = projection.cell(b.right, b.bottom, renderer);
                        [l, t, r, bt]
                    };
                    let head = head_surface_bounds(&sprite).map(cellr);
                    let plane = FaceTexture::bounds(&sprite).map(cellr);
                    let texture = FaceTexture::new(&sprite);
                    let stroke = face_ink(&sprite, &colors, InkRegion::TextBox);
                    let pixel_atlas = renderer.packing() != (1, 1)
                        && !(renderer == Renderer::HalfBlock && stroke != colors.ink(0));
                    let mut line = format!(
                        "{renderer:?} {depth:?} z{zoom} f{frame} {text} scale={:.2}x{:.2}cells/px",
                        projection.scale / f64::from(dx),
                        projection.scale_y / f64::from(dy)
                    );
                    if let (Some(h), Some(p), Some(t)) = (head, plane, texture) {
                        let fraction = t.text_fraction();
                        let cols = p[2] - p[0];
                        let rows = p[3] - p[1];
                        let per_glyph = cols * fraction[0] / t.text.len() as f64;
                        let text_rows = rows * fraction[1];
                        let tier = face_lod(t, cols, rows, pixel_atlas);
                        let cx = (p[0] + p[2]) / 2.0;
                        let cy = (p[1] + p[3]) / 2.0;
                        let tb = [
                            cx - cols * fraction[0] / 2.0,
                            cy - rows * fraction[1] / 2.0,
                            cx + cols * fraction[0] / 2.0,
                            cy + rows * fraction[1] / 2.0,
                        ];
                        let anchor =
                            face_anchor(&sprite).map(|(ax, ay)| projection.cell(ax, ay, renderer));
                        let _ = write!(
                            line,
                            " aspect={:?} fraction=[{:.2},{:.2}] head=[{:.1},{:.1},{:.1},{:.1}] ({:.1}x{:.1}) plane=[{:.1},{:.1},{:.1},{:.1}] ({:.1}x{:.1}) textbox=[{:.1},{:.1},{:.1},{:.1}] per_glyph={per_glyph:.2} text_rows={text_rows:.2} tier={tier:?} atlas_ok={pixel_atlas} anchor={:?} viewport=[{},{},{},{}]",
                            t.aspect,
                            fraction[0], fraction[1],
                            h[0], h[1], h[2], h[3], h[2] - h[0], h[3] - h[1],
                            p[0], p[1], p[2], p[3], cols, rows,
                            tb[0], tb[1], tb[2], tb[3],
                            anchor.map(|(x, y)| (x as i32, y as i32)),
                            viewport.x, viewport.y, viewport.right(), viewport.bottom()
                        );
                        // Glyph ink bits: are they on the face mask, inside the viewport,
                        // and on a cell wholly on the face plane?
                        let mut texels = Vec::new();
                        for y in 0..u32::from(sprite.height()) {
                            for x in 0..u32::from(sprite.width()) {
                                if let Some(uv) = valid_face_uv(&sprite, x, y) {
                                    texels.push((x, y, uv));
                                }
                            }
                        }
                        let (pw, ph) = (
                            1.0 / (FaceTexture::bounds(&sprite).unwrap().width()),
                            1.0 / (FaceTexture::bounds(&sprite).unwrap().height()),
                        );
                        let scale = t.grid_per_uv();
                        let size = t.grid_box();
                        let mut per_glyph_stats = Vec::new();
                        for glyph in 0..t.text.len() {
                            let (mut bits, mut off_mask, mut off_view, mut partial) = (0, 0, 0, 0);
                            for gy in 0..9 {
                                for column in 0..5 {
                                    let gx = glyph as i32 * 8 + column;
                                    if !t.bit(gx, gy) {
                                        continue;
                                    }
                                    bits += 1;
                                    let uv = [
                                        (f64::from(gx) + 0.5 + 1.0 - size[0] / 2.0) / scale[0]
                                            + 0.5,
                                        (f64::from(gy) + 0.5 + 1.0 - size[1] / 2.0) / scale[1]
                                            + 0.5,
                                    ];
                                    let nearest = texels.iter().min_by(|a, b| {
                                        let da = ((a.2[0] - uv[0]) / pw).powi(2)
                                            + ((a.2[1] - uv[1]) / ph).powi(2);
                                        let db = ((b.2[0] - uv[0]) / pw).powi(2)
                                            + ((b.2[1] - uv[1]) / ph).powi(2);
                                        da.total_cmp(&db)
                                    });
                                    let Some(&(sx, sy, tuv)) = nearest else {
                                        continue;
                                    };
                                    if (tuv[0] - uv[0]).abs() > 1.5 * pw
                                        || (tuv[1] - uv[1]).abs() > 1.5 * ph
                                    {
                                        off_mask += 1;
                                        continue;
                                    }
                                    let (cx, cy) = projection.cell(
                                        f64::from(sx) + 0.5,
                                        f64::from(sy) + 0.5,
                                        renderer,
                                    );
                                    let (col, row) = (cx.floor() as i32, cy.floor() as i32);
                                    if col < i32::from(viewport.x)
                                        || col >= i32::from(viewport.right())
                                        || row < i32::from(viewport.y)
                                        || row >= i32::from(viewport.bottom())
                                    {
                                        off_view += 1;
                                        continue;
                                    }
                                    let full = (0..dy).all(|py| {
                                        (0..dx).all(|px| {
                                            projected_face_uv(
                                                &sprite,
                                                projection,
                                                col as u32 * u32::from(dx) + u32::from(px),
                                                row as u32 * u32::from(dy) + u32::from(py),
                                            )
                                            .is_some()
                                        })
                                    });
                                    if !full {
                                        partial += 1;
                                    }
                                }
                            }
                            per_glyph_stats.push(format!(
                                "{}:bits{bits}/offmask{off_mask}/offview{off_view}/partialcell{partial}",
                                char::from(t.text.as_bytes()[glyph])
                            ));
                        }
                        let _ = write!(line, " glyphs[{}]", per_glyph_stats.join(" "));
                    }
                    // Paint like RoomWidget and capture the face layer.
                    let mut buf = Buffer::empty(whole);
                    let habitat = HabitatView {
                        state: &game.habitat,
                        rules: &config.habitat,
                        wins: game.wins,
                        age: game.age_ticks,
                    };
                    RoomWidget {
                        world: Some(&config.world),
                        sprite: &sprite,
                        detail: SpriteStyle::SixteenBit,
                        renderer,
                        scale: 0,
                        colors: &colors,
                        expression: Expression::Idle,
                        frame,
                        position: (500, 700),
                        camera,
                        habitat: Some(habitat),
                    }
                    .render(area, &mut buf);
                    let mut before = buf.clone();
                    // Re-derive the pre-face state: body only.
                    draw_projection(
                        &sprite,
                        renderer,
                        &colors,
                        projection,
                        viewport,
                        &mut before,
                    );
                    let mut after = before.clone();
                    let painted = draw_camera_face(
                        &sprite, renderer, &colors, projection, viewport, &mut after,
                    );
                    let changed = before
                        .content
                        .iter()
                        .zip(&after.content)
                        .filter(|(a, b)| a != b)
                        .count();
                    let _ = writeln!(out, "{line} painted={painted} face_cells={changed}");
                    if depth != Depth::Mono && frame == 0
                        || (depth == Depth::Rgb && frame != 0 && renderer == Renderer::HalfBlock)
                    {
                        for row in area.y..area.bottom() {
                            let mut text_row = String::from("  |");
                            let mut face_row = String::from("|");
                            for col in area.x..area.right() {
                                text_row.push_str(buf[(col, row)].symbol());
                                face_row.push_str(if before[(col, row)] != after[(col, row)] {
                                    after[(col, row)].symbol()
                                } else {
                                    " "
                                });
                            }
                            let _ = writeln!(out, "{text_row}| face{face_row}|");
                        }
                    }
                }
            }
        }
    }
    let path = std::path::Path::new("/mios-proof/live-face-layout.txt");
    if std::fs::write(path, &out).is_err() {
        println!("{out}");
    }
}
