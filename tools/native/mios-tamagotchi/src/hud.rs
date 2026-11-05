// AI-hint: Cell-budgeted golden-ratio HUD geometry and exact compact status labels.
// AI-related: ui.rs, config.rs, usr/share/mios/mios.toml [mcp.tmux.workspace]
use crate::{capabilities::Renderer, game::Game, sprite::SpriteStyle};
use ratatui::layout::Rect;

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
/// '*' is exactly100. Micro frames page complete metrics instead of truncating
/// values or inventing inaccurate percentages.
pub fn needs(game: &Game, width: u16, page: usize) -> String {
    let f = number(game.fullness);
    let h = number(game.happiness);
    let c = number(game.hygiene);
    let e = number(game.energy);
    let health = number(game.health);
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
