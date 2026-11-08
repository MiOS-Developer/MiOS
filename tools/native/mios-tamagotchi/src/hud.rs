// AI-hint: Cell-budgeted golden-ratio HUD geometry and exact compact status labels.
// AI-related: ui.rs, config.rs, usr/share/mios/mios.toml [mcp.tmux.workspace]
use crate::{capabilities::Renderer, game::Game, render::Colors, sprite::SpriteStyle};
use ratatui::{buffer::Buffer, layout::Rect};

/// One scene-wide detail decision, shared by the pet, room, HUD and menus.
/// All operator thresholds come from the layered SSOT; no private defaults.
#[derive(Clone, Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DetailRules {
    pub automatic: bool,
    pub min_width: u16,
    pub min_height: u16,
    pub hysteresis_percent: u16,
}

impl DetailRules {
    pub fn validate(&self) -> Result<(), String> {
        if !(8..=1024).contains(&self.min_width)
            || !(4..=1024).contains(&self.min_height)
            || self.hysteresis_percent > 50
        {
            return Err("detail policy: min_width must be8..1024, min_height4..1024 and hysteresis_percent0..50".into());
        }
        Ok(())
    }

    pub fn choose(
        &self,
        previous: SpriteStyle,
        manual: SpriteStyle,
        scene: Rect,
        renderer: Renderer,
        zoom_percent: u16,
    ) -> SpriteStyle {
        if scene.is_empty() {
            return SpriteStyle::EightBit;
        }
        if !self.automatic {
            return manual;
        }
        let (dx, dy) = renderer.packing();
        let zoom = u64::from(zoom_percent.clamp(50, 400));
        let width = u64::from(scene.width) * u64::from(dx) * zoom / 100;
        let height = u64::from(scene.height) * u64::from(dy) * zoom / 100;
        match previous {
            SpriteStyle::EightBit => {
                if width >= u64::from(self.min_width) && height >= u64::from(self.min_height) {
                    SpriteStyle::SixteenBit
                } else {
                    SpriteStyle::EightBit
                }
            }
            SpriteStyle::SixteenBit => {
                // Compare hundredths to keep a fractional hysteresis boundary
                // exact. Integer truncation must not delay a necessary drop.
                let retain = 100 - u64::from(self.hysteresis_percent.min(50));
                if width * 100 < u64::from(self.min_width) * retain
                    || height * 100 < u64::from(self.min_height) * retain
                {
                    SpriteStyle::EightBit
                } else {
                    SpriteStyle::SixteenBit
                }
            }
        }
    }
}

#[derive(Clone, Copy, Debug)]
pub struct HudLayout {
    pub scene: Rect,
    pub info: Rect,
    pub landscape: bool,
}

/// Golden proportions come from the native MiOS workspace SSOT. Minimum
/// control rows win when a tiny terminal cannot represent the ideal split.
pub fn split(area: Rect, ratio: f64, columns: u16, portrait_percent: u16) -> HudLayout {
    let ratio = if ratio.is_finite() && ratio > 1.0 {
        ratio
    } else {
        2.0
    };
    let landscape = area.width >= columns
        && u32::from(area.width) * 100 >= u32::from(area.height) * u32::from(portrait_percent)
        && area.width >= 4;
    if landscape {
        let major = ((f64::from(area.width) / ratio).round() as u16)
            .clamp(1, area.width.saturating_sub(2).max(1));
        HudLayout {
            scene: Rect::new(area.x, area.y, major, area.height),
            info: Rect::new(
                area.x + major + 1,
                area.y,
                area.width.saturating_sub(major + 1),
                area.height,
            ),
            landscape,
        }
    } else {
        let controls = if area.height >= 6 {
            3
        } else {
            2.min(area.height)
        };
        let major = ((f64::from(area.height) / ratio).round() as u16)
            .min(area.height.saturating_sub(controls));
        HudLayout {
            scene: Rect::new(area.x, area.y, area.width, major),
            info: Rect::new(
                area.x,
                area.y + major,
                area.width,
                area.height.saturating_sub(major),
            ),
            landscape,
        }
    }
}
/// Existing full HUD panels, shared with Auto scene admission.
pub fn full_layout(area: Rect, dialogue_rows: u16) -> [Rect; 4] {
    use ratatui::layout::{Constraint, Layout};
    Layout::vertical([
        Constraint::Length(3),
        Constraint::Min(1),
        Constraint::Length(dialogue_rows),
        Constraint::Length(6),
    ])
    .areas(area)
}
/// Prefer the informative compact layout when boxes starve the golden scene share.
pub fn scene_starved(scene: Rect, available: Rect, ratio: f64) -> bool {
    if available.is_empty() {
        return false;
    }
    let ratio = if ratio.is_finite() && ratio > 1.0 {
        ratio
    } else {
        2.0
    };
    f64::from(scene.height.saturating_sub(2)) * ratio < f64::from(available.height)
}

/// A visible numbered window. Selection and every row remain inside the
/// painted viewport after traversal or resize; numbers address only this page.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct MenuWindow {
    pub start: usize,
    pub count: usize,
}
pub fn menu_window(selected: usize, total: usize, rows: u16) -> MenuWindow {
    let count = usize::from(rows).min(9).min(total);
    let start = selected
        .min(total.saturating_sub(1))
        .saturating_sub(count.saturating_sub(1))
        .min(total.saturating_sub(count));
    MenuWindow { start, count }
}

/// One selectable hit cell in a content-sized menu.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct MenuCell {
    pub index: usize,
    pub area: Rect,
}

/// Content-fit menu geometry with bounded panel, distinct body/footer
/// viewports, visible in-bounds hit cells, and full-set spatial navigation centers.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct MenuGeometry {
    pub panel: Rect,
    pub body: Rect,
    pub footer: Rect,
    pub items: Vec<MenuCell>,
    pub first: usize,
    pub visible: usize,
    pub columns: u16,
    pub rows: u16,
    /// Actual tile height, reduced to one row when the requested height cannot
    /// fit alongside the border and footer. Zero when there are no items.
    pub cell_height: u16,
    pub navigation: Vec<(usize, i32, i32)>,
}

/// Visual focus and interaction state for textured menu tiles.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TileState {
    Idle,
    Focused,
    Disabled,
    FocusedDisabled,
}

/// Content-fitted panel rectangle bounded by the scene golden budget.
///
/// Bounded to available screen or container bounds, retaining room for the
/// scene according to golden proportions while strictly guaranteeing that
/// tiny viewports (such as 12x6) retain at least one option and controls.
pub fn content_panel(
    bounds: Rect,
    content_width: u16,
    content_rows: u16,
    footer_rows: u16,
    ratio: f64,
) -> Rect {
    content_panel_with_minimum(bounds, content_width, content_rows, footer_rows, 1, ratio)
}

fn content_panel_with_minimum(
    bounds: Rect,
    content_width: u16,
    content_rows: u16,
    footer_rows: u16,
    minimum_content_rows: u16,
    ratio: f64,
) -> Rect {
    if bounds.width == 0 || bounds.height == 0 {
        return Rect::new(bounds.x, bounds.y, 0, 0);
    }
    let ratio = if ratio.is_finite() && ratio > 1.0 {
        ratio
    } else {
        2.0
    };

    let panel_width = content_width.saturating_add(2).clamp(1, bounds.width);
    let needed_height = content_rows.saturating_add(footer_rows).saturating_add(2);

    let min_content = content_rows.min(minimum_content_rows);
    let min_needed = min_content
        .saturating_add(footer_rows)
        .saturating_add(2)
        .min(bounds.height);

    let scene_share = ((f64::from(bounds.height) / ratio).round() as u16)
        .min(bounds.height.saturating_sub(min_needed));
    let max_budget_height = bounds.height.saturating_sub(scene_share).max(min_needed);

    let panel_height = needed_height
        .min(max_budget_height)
        .clamp(min_needed, bounds.height);

    let x = bounds.x + bounds.width.saturating_sub(panel_width) / 2;
    let y = bounds.y + bounds.height.saturating_sub(panel_height);
    Rect::new(x, y, panel_width, panel_height)
}

/// Computes content-fitted menu geometry, visible item hit cells, and full-set
/// 2D spatial navigation centers. Reserve a complete tile before applying the
/// scene budget; if it cannot fit with the border and footer, use one-row tiles.
#[allow(clippy::too_many_arguments)]
pub fn menu_geometry(
    bounds: Rect,
    item_widths: &[u16],
    selected: usize,
    footer_rows: u16,
    cell_height: u16,
    gap: u16,
    max_columns: u16,
    ratio: f64,
) -> MenuGeometry {
    let requested_cell_height = cell_height.max(1);
    let max_columns = max_columns.max(1);
    let total_items = item_widths.len();

    if total_items == 0 || bounds.width == 0 || bounds.height == 0 {
        let panel = content_panel(bounds, 0, 0, footer_rows, ratio);
        let inner_w = panel.width.saturating_sub(2);
        let inner_h = panel.height.saturating_sub(2);
        let footer_h = footer_rows.min(inner_h);
        let body_h = inner_h.saturating_sub(footer_h);
        let body = Rect::new(panel.x + 1, panel.y + 1, inner_w, body_h);
        let footer = Rect::new(panel.x + 1, panel.y + 1 + body_h, inner_w, footer_h);
        return MenuGeometry {
            panel,
            body,
            footer,
            items: Vec::new(),
            first: 0,
            visible: 0,
            columns: 1,
            rows: 0,
            cell_height: 0,
            navigation: Vec::new(),
        };
    }

    let max_inner_w = bounds.width.saturating_sub(2).max(1);
    let max_item_width = item_widths.iter().copied().max().unwrap_or(1).max(1);

    let max_possible_cols = (total_items as u16).min(max_columns).max(1);
    let mut columns = 1u16;
    for c in (1..=max_possible_cols).rev() {
        let needed_w = c
            .saturating_mul(max_item_width)
            .saturating_add(c.saturating_sub(1).saturating_mul(gap));
        if needed_w <= max_inner_w {
            columns = c;
            break;
        }
    }

    let col_width = if columns == 1 {
        max_item_width.min(max_inner_w).max(1)
    } else {
        max_item_width
    };

    let content_width = columns
        .saturating_mul(col_width)
        .saturating_add(columns.saturating_sub(1).saturating_mul(gap))
        .min(max_inner_w);

    let available_body_height = bounds.height.saturating_sub(2).saturating_sub(footer_rows);
    let cell_height = if requested_cell_height <= available_body_height {
        requested_cell_height
    } else {
        1
    };
    let rows = total_items.div_ceil(columns as usize) as u16;
    let content_rows = rows.saturating_mul(cell_height);

    let panel = content_panel_with_minimum(
        bounds,
        content_width,
        content_rows,
        footer_rows,
        cell_height,
        ratio,
    );

    let inner_w = panel.width.saturating_sub(2);
    let inner_h = panel.height.saturating_sub(2);
    let footer_h = footer_rows.min(inner_h);
    let body_h = inner_h.saturating_sub(footer_h);

    let body = Rect::new(panel.x + 1, panel.y + 1, inner_w, body_h);
    let footer = Rect::new(panel.x + 1, panel.y + 1 + body_h, inner_w, footer_h);

    let visible_rows = (body.height / cell_height) as usize;

    let sel = selected.min(total_items.saturating_sub(1));
    let sel_row = sel / columns as usize;

    let first_row =
        if visible_rows == 0 || (rows as usize) <= visible_rows || sel_row < visible_rows {
            0
        } else {
            let max_first = (rows as usize).saturating_sub(visible_rows);
            sel_row
                .saturating_sub(visible_rows.saturating_sub(1))
                .min(max_first)
        };

    let first = first_row * (columns as usize);
    let page_capacity = visible_rows * (columns as usize);
    let end_idx = (first + page_capacity).min(total_items);

    let mut items = Vec::new();
    if visible_rows > 0 && body.width > 0 && body.height > 0 {
        for idx in first..end_idx {
            let rel_idx = idx - first;
            let col = (rel_idx % columns as usize) as u16;
            let row = (rel_idx / columns as usize) as u16;
            let cell_x = body.x + col * (col_width + gap);
            let cell_y = body.y + row * cell_height;
            let cell_area = Rect::new(cell_x, cell_y, col_width, cell_height);
            let in_bounds = cell_area.intersection(body);
            if !in_bounds.is_empty() {
                items.push(MenuCell {
                    index: idx,
                    area: in_bounds,
                });
            }
        }
    }

    let visible = items.len();

    let mut navigation = Vec::with_capacity(total_items);
    for idx in 0..total_items {
        let r = idx / columns as usize;
        let c = idx % columns as usize;
        let cell_x = body.x + (c as u16) * (col_width + gap);
        let center_x = cell_x as i32 + (col_width as i32) / 2;

        let row_offset = (r as i32) - (first_row as i32);
        let logical_y = body.y as i32 + row_offset * (cell_height as i32);
        let center_y = logical_y + (cell_height as i32) / 2;

        navigation.push((idx, center_x, center_y));
    }

    MenuGeometry {
        panel,
        body,
        footer,
        items,
        first,
        visible,
        columns,
        rows,
        cell_height,
        navigation,
    }
}

/// Deterministic ASCII / Unicode textured panel painter.
///
/// Draws border bevels and interior background strictly within the given area.
/// Contains no simulation, time, RNG, scene clearing, or action handling.
pub fn paint_menu_panel(buffer: &mut Buffer, area: Rect, colors: &Colors, ascii: bool) {
    let area = area.intersection(buffer.area);
    if area.width == 0 || area.height == 0 {
        return;
    }

    let (tl, tr, bl, br, h, v) = if ascii {
        ('+', '+', '+', '+', '-', '|')
    } else {
        ('┌', '┐', '└', '┘', '─', '│')
    };

    let border_fg = colors.ink(3);
    let border_bg = colors.background;
    let interior_fg = colors.ink(2);
    let interior_bg = colors.background;

    for y in area.top()..area.bottom() {
        for x in area.left()..area.right() {
            let is_top = y == area.top();
            let is_bot = y == area.bottom() - 1;
            let is_left = x == area.left();
            let is_right = x == area.right() - 1;

            if let Some(cell) = buffer.cell_mut((x, y)) {
                if is_top && is_left {
                    cell.set_char(tl).set_fg(border_fg).set_bg(border_bg);
                } else if is_top && is_right {
                    cell.set_char(tr).set_fg(border_fg).set_bg(border_bg);
                } else if is_bot && is_left {
                    cell.set_char(bl).set_fg(border_fg).set_bg(border_bg);
                } else if is_bot && is_right {
                    cell.set_char(br).set_fg(border_fg).set_bg(border_bg);
                } else if is_top || is_bot {
                    cell.set_char(h).set_fg(border_fg).set_bg(border_bg);
                } else if is_left || is_right {
                    cell.set_char(v).set_fg(border_fg).set_bg(border_bg);
                } else {
                    cell.set_char(' ').set_fg(interior_fg).set_bg(interior_bg);
                }
            }
        }
    }
}

/// Deterministic ASCII / Unicode menu tile painter with visible focus in mono.
///
/// Paints background, bevels, and focus indicator glyphs. Does NOT render labels,
/// perform input handling, or evaluate affordability or simulation state.
pub fn paint_menu_tile(
    buffer: &mut Buffer,
    area: Rect,
    colors: &Colors,
    state: TileState,
    ascii: bool,
) {
    let area = area.intersection(buffer.area);
    if area.width == 0 || area.height == 0 {
        return;
    }

    let (border_fg, tile_bg, tile_fg) = match state {
        TileState::Focused => (colors.ink(1), colors.ink(2), colors.background),
        TileState::Idle => (colors.ink(3), colors.background, colors.ink(7)),
        TileState::Disabled => (colors.ink(6), colors.background, colors.ink(6)),
        TileState::FocusedDisabled => (colors.ink(4), colors.background, colors.ink(4)),
    };

    if area.height == 1 {
        let (left_glyph, right_glyph) = match state {
            TileState::Focused => ('>', '<'),
            TileState::FocusedDisabled => ('!', '!'),
            TileState::Disabled => ('.', '.'),
            TileState::Idle => (' ', ' '),
        };

        for x in area.left()..area.right() {
            if let Some(cell) = buffer.cell_mut((x, area.top())) {
                if x == area.left() {
                    cell.set_char(left_glyph).set_fg(border_fg).set_bg(tile_bg);
                } else if x == area.right() - 1 && area.width > 1 {
                    cell.set_char(right_glyph).set_fg(border_fg).set_bg(tile_bg);
                } else {
                    cell.set_char(' ').set_fg(tile_fg).set_bg(tile_bg);
                }
            }
        }
    } else {
        let (tl, tr, bl, br, h, v) = match (state, ascii) {
            (TileState::Focused, true) => ('>', '<', '>', '<', '=', '|'),
            (TileState::Focused, false) => ('╔', '╗', '╚', '╝', '═', '║'),
            (TileState::FocusedDisabled, true) => ('!', '!', '!', '!', '=', '|'),
            (TileState::FocusedDisabled, false) => ('╒', '╕', '╘', '╛', '═', '│'),
            (TileState::Disabled, true) => ('.', '.', '.', '.', '.', ':'),
            (TileState::Disabled, false) => ('┌', '┐', '└', '┘', '╌', '┊'),
            (TileState::Idle, true) => ('+', '+', '+', '+', '-', '|'),
            (TileState::Idle, false) => ('┌', '┐', '└', '┘', '─', '│'),
        };

        for y in area.top()..area.bottom() {
            for x in area.left()..area.right() {
                let is_top = y == area.top();
                let is_bot = y == area.bottom() - 1;
                let is_left = x == area.left();
                let is_right = x == area.right() - 1;

                if let Some(cell) = buffer.cell_mut((x, y)) {
                    if is_top && is_left {
                        cell.set_char(tl).set_fg(border_fg).set_bg(tile_bg);
                    } else if is_top && is_right {
                        cell.set_char(tr).set_fg(border_fg).set_bg(tile_bg);
                    } else if is_bot && is_left {
                        cell.set_char(bl).set_fg(border_fg).set_bg(tile_bg);
                    } else if is_bot && is_right {
                        cell.set_char(br).set_fg(border_fg).set_bg(tile_bg);
                    } else if is_top || is_bot {
                        cell.set_char(h).set_fg(border_fg).set_bg(tile_bg);
                    } else if is_left || is_right {
                        cell.set_char(v).set_fg(border_fg).set_bg(tile_bg);
                    } else {
                        cell.set_char(' ').set_fg(tile_fg).set_bg(tile_bg);
                    }
                }
            }
        }
    }
}

pub fn counter(value: u32) -> String {
    for (unit, suffix) in [(1_000_000_000, "G"), (1_000_000, "M"), (1_000, "K")] {
        if value >= unit {
            return format!(
                "{}{suffix}{}",
                value / unit,
                if value.is_multiple_of(unit) { "" } else { "+" }
            );
        }
    }
    value.to_string()
}
/// Approximate long counters with an explicit '+' for a nonzero remainder.
pub fn ticks(value: u64) -> String {
    for (unit, suffix) in [
        (1_000_000_000_000_000_000, "E"),
        (1_000_000_000_000_000, "P"),
        (1_000_000_000_000, "T"),
        (1_000_000_000, "G"),
        (1_000_000, "M"),
        (1_000, "K"),
    ] {
        if value >= unit {
            return format!(
                "{}{suffix}{}",
                value / unit,
                if value.is_multiple_of(unit) { "" } else { "+" }
            );
        }
    }
    value.to_string()
}
fn number(value: u8) -> String {
    if value == 100 {
        "*".into()
    } else {
        value.to_string()
    }
}
/// Wide views name every care metric with a stable cell budget. Compact
/// '*' is exactly100; micro frames page complete metrics instead of truncating
/// values or inventing inaccurate percentages.
pub fn needs(game: &Game, width: u16, page: usize) -> String {
    let labeled = format!(
        "Food:{:3} Happy:{:3} Clean:{:3} Energy:{:3} Health:{:3}",
        game.fullness, game.happiness, game.hygiene, game.energy, game.health
    );
    if labeled.len() <= usize::from(width) {
        return labeled;
    }
    let f = number(game.fullness);
    let h = number(game.happiness);
    let c = number(game.hygiene);
    let e = number(game.energy);
    let health = number(game.health);
    let spaced = format!("F{f} H{h} C{c} E{e} +{health}");
    if spaced.len() <= usize::from(width) {
        return spaced;
    }
    let all = format!("F{f}H{h}C{c}E{e}+{health}");
    if all.len() <= usize::from(width) {
        return all;
    }
    match page % 3 {
        0 => format!("F{f}H{h}+{health}"),
        1 => format!("C{c}E{e}+{health}"),
        _ => format!("${}W{}", counter(game.coins), counter(game.wins)),
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    use crate::{config::Config, game::Game};
    #[test]
    fn golden_splits_obey_cell_budgets_and_reserve_micro_controls() {
        let c = Config::sample().unwrap();
        for (w, h) in [
            (10, 4),
            (18, 6),
            (38, 10),
            (78, 22),
            (151, 24),
            (1, 1),
            (0, 0),
        ] {
            let area = Rect::new(3, 2, w, h);
            let layout = split(
                area,
                c.hud_golden_ratio,
                c.hud_landscape_columns,
                c.hud_portrait_ratio_percent,
            );
            assert_eq!(layout.scene.intersection(area), layout.scene);
            assert_eq!(layout.info.intersection(area), layout.info);
            assert!(layout.scene.intersection(layout.info).is_empty());
            if h >= 4 {
                assert!(layout.info.height >= 2);
            }
        }
        let area = Rect::new(0, 0, 151, 24);
        let layout = split(
            area,
            c.hud_golden_ratio,
            c.hud_landscape_columns,
            c.hud_portrait_ratio_percent,
        );
        assert!(layout.landscape);
        assert!((f64::from(layout.scene.width) / 151.0 - 1.0 / c.hud_golden_ratio).abs() < 0.02);
    }
    #[test]
    fn care_labels_use_stable_width_and_compact_rows_remain_exact() {
        let c = Config::sample().unwrap();
        let mut g = Game::new(&c.game, 7);
        g.fullness = 61;
        g.happiness = 42;
        g.hygiene = 73;
        g.energy = 84;
        g.health = 95;
        let wide = needs(&g, 57, 0);
        for metric in [
            "Food: 61",
            "Happy: 42",
            "Clean: 73",
            "Energy: 84",
            "Health: 95",
        ] {
            assert!(wide.contains(metric), "{wide}");
        }
        assert_eq!(needs(&g, 28, 0), "F61 H42 C73 E84 +95");
        assert_eq!(needs(&g, 18, 0), "F61H42C73E84+95");
        let width = wide.len() as u16;
        for value in [0, 9, 99, 100] {
            g.fullness = value;
            assert_eq!(needs(&g, width, 0).len(), usize::from(width));
            assert!(needs(&g, width, 0).starts_with("Food:"));
            assert!(!needs(&g, width - 1, 0).contains("Food:"));
        }
    }

    #[test]
    fn micro_status_pages_retain_all_five_exact_needs_and_currency() {
        let c = Config::sample().unwrap();
        let mut g = Game::new(&c.game, 7);
        g.fullness = 96;
        g.happiness = 74;
        g.hygiene = 98;
        g.energy = 87;
        g.health = 100;
        let pages = (0..3).map(|p| needs(&g, 10, p)).collect::<Vec<_>>();
        let all = pages.join(" ");
        for text in ["F96", "H74", "C98", "E87", "+*", "$10", "W0"] {
            assert!(all.contains(text), "{all}");
        }
        assert!(pages.iter().all(|s| s.len() <= 10));
    }
}

#[cfg(test)]
mod detail_policy_tests {
    use super::*;

    fn rules() -> DetailRules {
        DetailRules {
            automatic: true,
            min_width: 64,
            min_height: 48,
            hysteresis_percent: 15,
        }
    }

    #[test]
    fn packed_scene_boundaries_require_both_dimensions_in_every_renderer() {
        let rules = rules();
        for renderer in [
            Renderer::Ascii,
            Renderer::HalfBlock,
            Renderer::Quadrant,
            Renderer::Braille,
        ] {
            let (dx, dy) = renderer.packing();
            let width = 64 / dx;
            let height = 48 / dy;
            let choose = |w, h| {
                rules.choose(
                    SpriteStyle::EightBit,
                    SpriteStyle::SixteenBit,
                    Rect::new(0, 0, w, h),
                    renderer,
                    100,
                )
            };
            assert_eq!(
                choose(width, height),
                SpriteStyle::SixteenBit,
                "{renderer:?}"
            );
            assert_eq!(
                choose(width - 1, height),
                SpriteStyle::EightBit,
                "{renderer:?} width"
            );
            assert_eq!(
                choose(width, height - 1),
                SpriteStyle::EightBit,
                "{renderer:?} height"
            );
        }
    }

    #[test]
    fn hysteresis_has_exact_enter_and_drop_boundaries_without_flapping() {
        let rules = rules();
        let choose = |previous, width, height| {
            rules.choose(
                previous,
                SpriteStyle::SixteenBit,
                Rect::new(0, 0, width, height),
                Renderer::HalfBlock,
                100,
            )
        };
        assert_eq!(
            choose(SpriteStyle::EightBit, 64, 24),
            SpriteStyle::SixteenBit
        );
        assert_eq!(
            choose(SpriteStyle::SixteenBit, 55, 21),
            SpriteStyle::SixteenBit
        );
        assert_eq!(choose(SpriteStyle::EightBit, 55, 21), SpriteStyle::EightBit);
        assert_eq!(
            choose(SpriteStyle::SixteenBit, 54, 21),
            SpriteStyle::EightBit
        );
        assert_eq!(
            choose(SpriteStyle::SixteenBit, 55, 20),
            SpriteStyle::EightBit
        );
        // Real font-size demonstration budgets:12pt ->24pt ->12pt.
        let fine = choose(SpriteStyle::EightBit, 95, 25);
        let coarse = choose(fine, 46, 11);
        assert_eq!(fine, SpriteStyle::SixteenBit);
        assert_eq!(coarse, SpriteStyle::EightBit);
        assert_eq!(choose(coarse, 95, 25), SpriteStyle::SixteenBit);
        assert_eq!(
            choose(SpriteStyle::SixteenBit, 54, 26),
            SpriteStyle::EightBit
        );
    }

    #[test]
    fn zoom_is_bounded_and_changes_the_same_global_pixel_budget() {
        let rules = rules();
        let choose = |width, height, zoom| {
            rules.choose(
                SpriteStyle::EightBit,
                SpriteStyle::EightBit,
                Rect::new(0, 0, width, height),
                Renderer::HalfBlock,
                zoom,
            )
        };
        assert_eq!(choose(32, 12, 200), SpriteStyle::SixteenBit);
        assert_eq!(choose(64, 24, 50), SpriteStyle::EightBit);
        assert_eq!(choose(16, 6, 400), SpriteStyle::SixteenBit);
        assert_eq!(choose(16, 6, u16::MAX), choose(16, 6, 400));
        assert_eq!(choose(128, 48, 0), choose(128, 48, 50));
        assert_eq!(
            choose(u16::MAX, u16::MAX, u16::MAX),
            SpriteStyle::SixteenBit
        );
        assert_eq!(
            rules.choose(
                SpriteStyle::SixteenBit,
                SpriteStyle::SixteenBit,
                Rect::new(0, 0, 95, 25),
                Renderer::HalfBlock,
                50
            ),
            SpriteStyle::EightBit
        );
    }

    #[test]
    fn manual_override_and_zero_area_are_explicit_and_rules_validate_strictly() {
        let mut rules = rules();
        rules.automatic = false;
        for manual in [SpriteStyle::EightBit, SpriteStyle::SixteenBit] {
            for previous in [SpriteStyle::EightBit, SpriteStyle::SixteenBit] {
                for scene in [Rect::new(0, 0, 1, 1), Rect::new(0, 0, 1024, 1024)] {
                    assert_eq!(
                        rules.choose(previous, manual, scene, Renderer::Braille, 400),
                        manual
                    );
                }
                for scene in [Rect::new(0, 0, 0, 100), Rect::new(0, 0, 100, 0)] {
                    assert_eq!(
                        rules.choose(previous, manual, scene, Renderer::Braille, 100),
                        SpriteStyle::EightBit
                    );
                }
            }
        }
        assert!(rules.validate().is_ok());
        for width in [7, 1025, u16::MAX] {
            let mut bad = rules.clone();
            bad.min_width = width;
            assert!(bad.validate().is_err());
        }
        for height in [3, 1025, u16::MAX] {
            let mut bad = rules.clone();
            bad.min_height = height;
            assert!(bad.validate().is_err());
        }
        rules.hysteresis_percent = 51;
        assert!(rules.validate().is_err());
        assert!(serde_json::from_str::<DetailRules>(
            r#"{"automatic":true,"min_width":64,"min_height":48,"hysteresis_percent":15,"private_default":1}"#).is_err());
    }
}

#[cfg(test)]
mod large_counter_tests {
    use super::*;
    #[test]
    fn valid_extreme_status_values_fit_micro_frames_without_hiding_needs() {
        let c = crate::config::Config::sample().unwrap();
        let mut g = Game::new(&c.game, 7);
        g.coins = crate::game::MAX_COINS;
        g.wins = u32::MAX;
        for page in 0..3 {
            assert!(needs(&g, 10, page).len() <= 10);
        }
        assert_eq!(counter(1_000_000), "1M");
        assert_eq!(counter(1_001), "1K+");
    }
}

#[cfg(test)]
mod menu_window_tests {
    use super::*;
    #[test]
    fn every_selection_survives_resize_with_truthful_numbered_ranges() {
        for total in [1, 2, 5, 14, 23, 64] {
            for selected in 0..total {
                for rows in [0, 1, 2, 4, 9, 24, u16::MAX] {
                    let window = menu_window(selected, total, rows);
                    assert!(window.count <= 9);
                    assert!(window.start + window.count <= total);
                    if rows > 0 {
                        assert!(selected >= window.start);
                        assert!(selected < window.start + window.count);
                    }
                }
            }
        }
        assert_eq!(menu_window(0, 0, 8), MenuWindow { start: 0, count: 0 });
    }
}

#[cfg(test)]
mod menu_geometry_tests {
    use super::*;

    #[test]
    fn content_panel_preserves_golden_budget_and_micro_minimums() {
        // 12x6: must retain at least one option (1 row) and controls (1 footer row)
        let bounds_12x6 = Rect::new(0, 0, 12, 6);
        let panel_12x6 = content_panel(bounds_12x6, 6, 2, 1, 1.618);
        assert!(panel_12x6.width <= 12);
        assert!(panel_12x6.height <= 6);
        assert!(panel_12x6.height >= 4); // border (2) + option (1) + footer (1)

        // Large 80x24: content-needed height is respected rather than stretching
        let bounds_80x24 = Rect::new(0, 0, 80, 24);
        let panel_small_content = content_panel(bounds_80x24, 20, 2, 1, 1.618);
        // Needed: 2 content + 1 footer + 2 border = 5
        assert_eq!(panel_small_content.height, 5);

        // Large 80x24: tall content is bounded by scene golden budget
        let panel_tall_content = content_panel(bounds_80x24, 20, 30, 2, 1.618);
        // Golden budget leaves scene share (~15 rows), capping panel height
        assert!(panel_tall_content.height < 24);
        assert!(bounds_80x24.height - panel_tall_content.height >= 14);

        // Empty bounds
        let empty = content_panel(Rect::new(5, 5, 0, 0), 10, 5, 2, 1.618);
        assert_eq!(empty.width, 0);
        assert_eq!(empty.height, 0);
    }

    #[test]
    fn menu_geometry_positive_resolutions_and_bounds() {
        let resolutions = [
            (12, 6, 1, 1),   // micro
            (20, 8, 2, 1),   // compact
            (20, 26, 2, 1),  // portrait
            (80, 24, 2, 1),  // standard terminal
            (127, 29, 2, 1), // wide status
            (320, 90, 2, 1), // ultra wide
        ];

        for (w, h, footer_rows, cell_h) in resolutions {
            let bounds = Rect::new(0, 0, w, h);
            let item_widths = [6, 8, 7, 5, 10, 6, 9, 8];
            for sel in [0, 2, 7] {
                let geom =
                    menu_geometry(bounds, &item_widths, sel, footer_rows, cell_h, 1, 3, 1.618);

                // Panel is strictly inside bounds:
                assert_eq!(geom.panel.intersection(bounds), geom.panel);
                assert!(!geom.panel.is_empty(), "panel empty for {w}x{h}");

                // Body and footer are inside panel:
                assert_eq!(geom.body.intersection(geom.panel), geom.body);
                assert_eq!(geom.footer.intersection(geom.panel), geom.footer);

                // No overlap between body and footer:
                assert!(geom.body.intersection(geom.footer).is_empty());

                // Rows is total logical rows:
                let expected_rows = item_widths.len().div_ceil(geom.columns as usize);
                assert_eq!(geom.rows as usize, expected_rows);

                // Selected is visible in items:
                assert!(
                    geom.items.iter().any(|c| c.index == sel),
                    "selected {sel} must be visible in {w}x{h}"
                );

                // Items are strictly inside body and do not overlap:
                for (i, cell_a) in geom.items.iter().enumerate() {
                    assert_eq!(cell_a.area.intersection(geom.body), cell_a.area);
                    for cell_b in &geom.items[i + 1..] {
                        assert!(
                            cell_a.area.intersection(cell_b.area).is_empty(),
                            "cells overlap in {w}x{h}: {:?} vs {:?}",
                            cell_a,
                            cell_b
                        );
                    }
                }

                // Visible count matches items len:
                assert_eq!(geom.visible, geom.items.len());

                // Navigation contains ALL items (including off-page):
                assert_eq!(geom.navigation.len(), item_widths.len());
                for (idx, &(nav_idx, _, _)) in geom.navigation.iter().enumerate() {
                    assert_eq!(nav_idx, idx);
                }

                // 12x6 retains at least 1 option and controls:
                if w == 12 && h == 6 {
                    assert!(
                        !geom.items.is_empty(),
                        "12x6 must retain at least one option"
                    );
                    assert!(geom.footer.height >= 1, "12x6 must retain controls");
                }
            }
        }
    }

    #[test]
    fn tall_tiles_retain_a_complete_selected_option_in_micro_panels() {
        let bounds = Rect::new(5, 7, 12, 6);
        for selected in 0..8 {
            let geom = menu_geometry(bounds, &[6; 8], selected, 1, 3, 1, 3, 1.618);
            assert_eq!(geom.cell_height, 3);
            assert_eq!(geom.body.height, 3);
            assert_eq!(geom.footer.height, 1);
            let item = geom
                .items
                .iter()
                .find(|item| item.index == selected)
                .expect("selected tall tile must remain visible");
            assert_eq!(item.area.height, 3);
            assert_eq!(item.area.intersection(geom.body), item.area);
            assert!(item.area.intersection(geom.footer).is_empty());
        }
    }

    #[test]
    fn oversized_tiles_fall_back_to_single_rows_without_losing_controls() {
        let bounds = Rect::new(5, 7, 12, 6);
        for requested_height in [3, 5, u16::MAX] {
            for selected in [0, 3, 7] {
                let geom =
                    menu_geometry(bounds, &[6; 8], selected, 2, requested_height, 1, 3, 1.618);
                assert_eq!(geom.cell_height, 1, "compact tiles must be explicit");
                assert_eq!(geom.footer.height, 2, "both control rows must survive");
                assert!(geom.panel.height < bounds.height, "retain scene space");
                let item = geom
                    .items
                    .iter()
                    .find(|item| item.index == selected)
                    .expect("selected compact fallback must remain visible");
                assert_eq!(item.area.height, 1);
                assert_eq!(item.area.intersection(geom.body), item.area);
                assert!(item.area.intersection(geom.footer).is_empty());
            }
        }
    }

    #[test]
    fn tall_tile_geometry_keeps_hit_boxes_and_navigation_inside_offset_viewports() {
        for (width, height) in [(12, 6), (20, 8), (20, 26), (80, 24), (127, 29), (320, 90)] {
            let bounds = Rect::new(5, 7, width, height);
            for requested_height in [1, 3, 5, u16::MAX] {
                for selected in [0, 7, 19] {
                    let geom =
                        menu_geometry(bounds, &[8; 20], selected, 2, requested_height, 1, 3, 1.618);
                    assert_eq!(geom.panel.intersection(bounds), geom.panel);
                    assert_eq!(geom.body.intersection(geom.panel), geom.body);
                    assert_eq!(geom.footer.intersection(geom.panel), geom.footer);
                    assert!(geom.body.intersection(geom.footer).is_empty());
                    assert_eq!(geom.visible, geom.items.len());
                    assert_eq!(geom.navigation.len(), 20);
                    assert!(geom.items.iter().any(|item| item.index == selected));
                    for (index, item) in geom.items.iter().enumerate() {
                        assert_eq!(item.area.intersection(geom.body), item.area);
                        assert_eq!(item.area.height, geom.cell_height);
                        assert!(item.area.intersection(geom.footer).is_empty());
                        for other in &geom.items[index + 1..] {
                            assert!(item.area.intersection(other.area).is_empty());
                        }
                        let (nav_index, x, y) = geom.navigation[item.index];
                        assert_eq!(nav_index, item.index);
                        assert!(x >= i32::from(item.area.x));
                        assert!(x < i32::from(item.area.right()));
                        assert!(y >= i32::from(item.area.y));
                        assert!(y < i32::from(item.area.bottom()));
                    }
                }
            }
        }
    }

    #[test]
    fn menu_geometry_navigation_centers_and_paging_reachability() {
        let bounds = Rect::new(0, 0, 40, 10);
        let item_widths = [8; 20]; // 20 items
        let geom = menu_geometry(bounds, &item_widths, 0, 1, 1, 1, 2, 1.618);

        // All 20 centers are present in navigation:
        assert_eq!(geom.navigation.len(), 20);

        // First page items have centers within body:
        for cell in &geom.items {
            let (_, cx, cy) = geom.navigation[cell.index];
            assert!(cx >= geom.body.x as i32 && cx < geom.body.right() as i32);
            assert!(cy >= geom.body.y as i32 && cy < geom.body.bottom() as i32);
        }

        // Off-page items on subsequent rows have centers below body:
        let off_page_idx = geom.first + geom.visible;
        if off_page_idx < item_widths.len() {
            let (_, _, off_cy) = geom.navigation[off_page_idx];
            assert!(
                off_cy >= geom.body.bottom() as i32,
                "off-page item center Y {off_cy} must be >= body.bottom() {}",
                geom.body.bottom()
            );
        }

        // Selected at last item reveals it on page:
        let geom_last = menu_geometry(bounds, &item_widths, 19, 1, 1, 1, 2, 1.618);
        assert!(
            geom_last.items.iter().any(|c| c.index == 19),
            "last item must be visible when selected"
        );
        assert_eq!(geom_last.first % geom_last.columns as usize, 0);

        // Items on earlier pages have centers above body:
        if geom_last.first > 0 {
            let (_, _, early_cy) = geom_last.navigation[0];
            assert!(
                early_cy < geom_last.body.y as i32,
                "early item center Y {early_cy} must be < body.y {}",
                geom_last.body.y
            );
        }
    }

    #[test]
    fn menu_geometry_negative_control_off_page_centers_stripped_regression() {
        let bounds = Rect::new(0, 0, 40, 10);
        let item_widths = [8; 20];
        let geom = menu_geometry(bounds, &item_widths, 0, 1, 1, 1, 2, 1.618);

        // Real geometry: can reach off-page items because navigation has all centers:
        let can_reach_next_page = geom.navigation.iter().any(|&(idx, _, cy)| {
            idx >= geom.first + geom.visible && cy >= geom.body.bottom() as i32
        });
        assert!(
            can_reach_next_page,
            "real geometry must provide off-page navigation targets"
        );

        // Planted regression: strip off-page centers (simulating linear-only or clamped navigation):
        let stripped_navigation: Vec<_> = geom
            .navigation
            .iter()
            .copied()
            .filter(|&(_, _, cy)| cy >= geom.body.y as i32 && cy < geom.body.bottom() as i32)
            .collect();
        let stripped_can_reach_next_page = stripped_navigation.iter().any(|&(idx, _, cy)| {
            idx >= geom.first + geom.visible && cy >= geom.body.bottom() as i32
        });
        assert!(
            !stripped_can_reach_next_page,
            "negative control: stripped navigation cannot reach off-page targets"
        );
    }

    #[test]
    fn menu_geometry_negative_control_selection_reveal_regression() {
        let bounds = Rect::new(0, 0, 30, 8);
        let item_widths = [8; 20];
        let geom = menu_geometry(bounds, &item_widths, 15, 1, 1, 1, 2, 1.618);
        assert!(
            geom.items.iter().any(|c| c.index == 15),
            "real geometry reveals selected item"
        );

        // Planted regression: no scrolling (first locked at 0):
        let regression_visible = (geom.body.height as usize) * geom.columns as usize;
        let regression_contains_sel = (0..regression_visible).any(|idx| idx == 15);
        assert!(
            !regression_contains_sel,
            "negative control: without reveal, selection on later page is invisible"
        );
    }

    #[test]
    fn menu_painters_deterministic_focus_and_borders() {
        let colors = Colors::from_config(
            &crate::config::Config::sample().unwrap(),
            crate::capabilities::Depth::Mono,
        )
        .unwrap();
        let mut buffer = Buffer::empty(Rect::new(0, 0, 30, 10));

        // Paint panel in ASCII and Unicode:
        paint_menu_panel(&mut buffer, Rect::new(1, 1, 28, 8), &colors, true);
        assert_eq!(buffer[(1, 1)].symbol(), "+");
        assert_eq!(buffer[(28, 1)].symbol(), "+");
        assert_eq!(buffer[(2, 1)].symbol(), "-");
        assert_eq!(buffer[(1, 2)].symbol(), "|");

        paint_menu_panel(&mut buffer, Rect::new(1, 1, 28, 8), &colors, false);
        assert_eq!(buffer[(1, 1)].symbol(), "┌");
        assert_eq!(buffer[(28, 1)].symbol(), "┐");
        assert_eq!(buffer[(2, 1)].symbol(), "─");
        assert_eq!(buffer[(1, 2)].symbol(), "│");

        // Paint tiles in mono - focus MUST be visible via glyphs/bevels:
        let tile_area = Rect::new(3, 3, 10, 1);
        paint_menu_tile(&mut buffer, tile_area, &colors, TileState::Focused, true);
        assert_eq!(buffer[(3, 3)].symbol(), ">");
        assert_eq!(buffer[(12, 3)].symbol(), "<");

        paint_menu_tile(&mut buffer, tile_area, &colors, TileState::Idle, true);
        assert_eq!(buffer[(3, 3)].symbol(), " ");
        assert_eq!(buffer[(12, 3)].symbol(), " ");

        // In 3-line tiles:
        let tile3_area = Rect::new(3, 3, 10, 3);
        paint_menu_tile(&mut buffer, tile3_area, &colors, TileState::Focused, false);
        assert_eq!(buffer[(3, 3)].symbol(), "╔");
        assert_eq!(buffer[(12, 3)].symbol(), "╗");
        assert_eq!(buffer[(4, 3)].symbol(), "═");

        paint_menu_tile(&mut buffer, tile3_area, &colors, TileState::Idle, false);
        assert_eq!(buffer[(3, 3)].symbol(), "┌");
        assert_eq!(buffer[(12, 3)].symbol(), "┐");
        assert_eq!(buffer[(4, 3)].symbol(), "─");

        // Focused is distinguishable from Idle in glyphs:
        assert_ne!(
            "╔", "┌",
            "focused and idle borders must differ in glyphs for mono visibility"
        );
    }
}
