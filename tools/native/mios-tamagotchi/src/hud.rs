// AI-hint: Cell-budgeted golden-ratio HUD geometry and exact compact status labels.
// AI-related: ui.rs, config.rs, usr/share/mios/mios.toml [mcp.tmux.workspace]
use crate::game::Game;
use ratatui::layout::Rect;

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
pub fn counter(value: u32) -> String {
    for (unit, suffix) in [(1_000_000_000, "G"), (1_000_000, "M"), (1_000, "K")] {
        if value >= unit {
            return format!(
                "{}{suffix}{}",
                value / unit,
                if value % unit == 0 { "" } else { "+" }
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
