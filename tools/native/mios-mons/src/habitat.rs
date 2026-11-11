// AI-hint: Deterministic cosmetic rooms, validated SSOT catalogues and explicit decoration transactions.
// AI-related: game.rs, save.rs, config.rs, usr/share/mios/mios.toml [mons.habitat]
use serde::{Deserialize, Serialize};
use std::sync::OnceLock;

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Theme {
    pub id: u8,
    pub name: String,
    pub wall: u8,
    pub floor: u8,
    pub accent: u8,
    /// 0 plain, 1 stripes, 2 checks, 3 dots, 4 waves, 5 leaves, 6 stars, 7 brick.
    pub pattern: u8,
}
#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Item {
    pub id: u8,
    pub name: String,
    pub price: u32,
    /// 0 plant, 1 seat, 2 table, 3 lamp, 4 rug, 5 shelf, 6 ornament, 7 trophy.
    pub kind: u8,
    pub slot: u8,
    #[serde(default)]
    pub reward_wins: u32,
    #[serde(default)]
    pub reward_age: u64,
}
impl Item {
    pub fn earned(&self) -> bool {
        self.reward_wins != 0 || self.reward_age != 0
    }
    pub fn eligible(&self, wins: u32, age: u64) -> bool {
        self.earned() && wins >= self.reward_wins && age >= self.reward_age
    }
}
#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HabitatRules {
    pub themes: Vec<Theme>,
    pub items: Vec<Item>,
}
impl HabitatRules {
    pub fn validate(&self) -> Result<(), String> {
        if !(8..=32).contains(&self.themes.len()) || !(12..=64).contains(&self.items.len()) {
            return Err("mons.habitat: require 8..=32 themes and 12..=64 items".into());
        }
        for (index, theme) in self.themes.iter().enumerate() {
            if usize::from(theme.id) != index
                || !valid_name(&theme.name)
                || [theme.wall, theme.floor, theme.accent]
                    .iter()
                    .any(|c| *c > 15)
                || theme.pattern > 7
            {
                return Err("mons.habitat: invalid theme ID, name, palette or pattern".into());
            }
        }
        for (index, item) in self.items.iter().enumerate() {
            if usize::from(item.id) != index
                || !valid_name(&item.name)
                || item.kind > 7
                || item.slot > 15
                || item.price > crate::game::MAX_COINS
                || item.reward_age > 1_000_000
                || item.reward_wins > 1_000_000
                || (item.earned() && item.price != 0)
                || (!item.earned() && item.price == 0)
            {
                return Err(
                    "mons.habitat: invalid item ID, name, kind, slot, price or reward".into(),
                );
            }
        }
        Ok(())
    }
    pub fn item(&self, id: u8) -> Option<&Item> {
        self.items.get(usize::from(id)).filter(|item| item.id == id)
    }
    pub fn theme(&self, id: u8) -> Option<&Theme> {
        self.themes
            .get(usize::from(id))
            .filter(|theme| theme.id == id)
    }
}
fn valid_name(name: &str) -> bool {
    !name.is_empty()
        && name.len() <= 48
        && name.trim() == name
        && name.bytes().all(|b| b.is_ascii_graphic() || b == b' ')
}

/// Compatibility callers use the embedded catalogue. Layered callers pass their
/// HabitatRules to the save/load variants explicitly.
pub fn vendor_rules() -> Result<&'static HabitatRules, String> {
    static RULES: OnceLock<Result<HabitatRules, String>> = OnceLock::new();
    RULES
        .get_or_init(|| {
            let value = include_str!("../../../../usr/share/mios/mios.toml")
                .parse::<toml::Value>()
                .map_err(|e| e.to_string())?;
            let rules: HabitatRules = value
                .get("mons")
                .and_then(|v| v.get("habitat"))
                .ok_or("embedded vendor SSOT lacks [mons.habitat]")?
                .clone()
                .try_into()
                .map_err(|e| format!("mons.habitat: {e}"))?;
            rules.validate()?;
            Ok(rules)
        })
        .as_ref()
        .map_err(Clone::clone)
}
#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Habitat {
    pub seed: u64,
    pub theme: u8,
    /// Permanent purchases only; earned eligibility is never written here.
    pub owned: u64,
    pub placed: u64,
}
impl Habitat {
    pub fn new(creation_seed: u64) -> Self {
        let seed = mix(creation_seed ^ 0x6861_6269_7461_7401).max(1);
        Self {
            seed,
            theme: (seed % 8) as u8,
            owned: 0,
            placed: 0,
        }
    }
    pub fn owns(&self, id: u8, wins: u32, age: u64, rules: &HabitatRules) -> bool {
        rules.item(id).is_some_and(|item| {
            if item.earned() {
                item.eligible(wins, age)
            } else {
                self.owned & bit(id) != 0
            }
        })
    }
    pub fn validate(&self, wins: u32, age: u64, rules: &HabitatRules) -> Result<(), String> {
        rules.validate()?;
        if self.seed == 0 || rules.theme(self.theme).is_none() {
            return Err("save contains invalid habitat seed or theme ID".into());
        }
        let mut purchases = 0;
        let mut eligible = 0;
        let mut occupied = 0u16;
        for item in &rules.items {
            let flag = bit(item.id);
            if !item.earned() {
                purchases |= flag;
            }
            if self.owns(item.id, wins, age, rules) {
                eligible |= flag;
            }
            if self.placed & flag != 0 {
                let slot = 1u16 << item.slot;
                if occupied & slot != 0 {
                    return Err("save places multiple habitat items in one slot".into());
                }
                occupied |= slot;
            }
        }
        if self.owned & !purchases != 0 || self.placed & !eligible != 0 {
            return Err("save contains unknown, unowned or unearned habitat items".into());
        }
        Ok(())
    }
    /// Stratified anchors in normalized 0..=1000 room coordinates. No mutable
    /// RNG, frame counter, game clock or wall clock participates in this layout.
    pub fn layout(&self, slot: u8) -> (u16, u16) {
        let slot = slot.min(15);
        let value = mix(self.seed ^ u64::from(slot).wrapping_mul(0x9e37_79b9_7f4a_7c15));
        let x = u16::from(slot % 4) * 250 + 60 + (value % 130) as u16;
        let y = u16::from(slot / 4) * 200 + 120 + ((value >> 32) % 100) as u16;
        (x, y)
    }
}
fn bit(id: u8) -> u64 {
    1u64.checked_shl(u32::from(id)).unwrap_or(0)
}
fn mix(mut value: u64) -> u64 {
    value = (value ^ (value >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
    value = (value ^ (value >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
    value ^ (value >> 31)
}

impl crate::game::Game {
    pub fn buy_item(&mut self, id: u8, rules: &HabitatRules) -> Result<String, String> {
        self.validate_habitat(rules)?;
        let item = rules.item(id).ok_or("Unknown decoration.")?;
        if item.earned() {
            return Err("Earn this decoration through play and growth.".into());
        }
        if self.habitat.owns(id, self.wins, self.age_ticks, rules) {
            return Err("You already own this decoration.".into());
        }
        let remaining = self
            .coins
            .checked_sub(item.price)
            .ok_or("Not enough coins.")?;
        self.coins = remaining;
        self.habitat.owned |= bit(id);
        Ok(format!(
            "Purchased {}. Choose it again to place it.",
            item.name
        ))
    }
    pub fn toggle_item(&mut self, id: u8, rules: &HabitatRules) -> Result<String, String> {
        self.validate_habitat(rules)?;
        let item = rules.item(id).ok_or("Unknown decoration.")?;
        if !self.habitat.owns(id, self.wins, self.age_ticks, rules) {
            return Err("Purchase or earn this decoration before placing it.".into());
        }
        let flag = bit(id);
        if self.habitat.placed & flag != 0 {
            self.habitat.placed &= !flag;
            return Ok(format!("Stored {}.", item.name));
        }
        for other in &rules.items {
            if other.slot == item.slot {
                self.habitat.placed &= !bit(other.id);
            }
        }
        self.habitat.placed |= flag;
        Ok(format!("Placed {}.", item.name))
    }
    pub fn set_theme(&mut self, id: u8, rules: &HabitatRules) -> Result<String, String> {
        self.validate_habitat(rules)?;
        let theme = rules.theme(id).ok_or("Unknown habitat theme.")?;
        self.habitat.theme = id;
        Ok(format!("Room theme: {}.", theme.name))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        config::Config,
        game::{Care, Game},
    };

    #[test]
    fn room_seeds_and_layout_are_stable_diverse_and_do_not_consume_game_rng() {
        let c = Config::sample().unwrap();
        let mut seeds = std::collections::BTreeSet::new();
        let mut themes = std::collections::BTreeSet::new();
        let mut anchors = std::collections::BTreeSet::new();
        for seed in 0..256 {
            let game = Game::new(&c.game, seed);
            assert_eq!(game, Game::new(&c.game, seed));
            assert_eq!(game.visual_seed(), seed);
            seeds.insert(game.habitat.seed);
            themes.insert(game.habitat.theme);
            anchors.insert(game.habitat.layout(0));
            let frozen = game.clone();
            for slot in 0..=255 {
                let (x, y) = game.habitat.layout(slot);
                assert!(x <= 1000 && y <= 1000);
            }
            assert_eq!(game, frozen);
        }
        assert_eq!(seeds.len(), 256);
        assert_eq!(themes.len(), 8);
        assert!(anchors.len() > 200);
        let mut decorated = Game::new(&c.game, 7);
        let mut control = decorated.clone();
        decorated.buy_item(0, &c.habitat).unwrap();
        decorated.toggle_item(0, &c.habitat).unwrap();
        decorated.set_theme(1, &c.habitat).unwrap();
        for _ in 0..c.game.hatch_ticks {
            decorated.tick(&c.game);
            control.tick(&c.game);
        }
        decorated.care(Care::Play, &c.game);
        control.care(Care::Play, &c.game);
        assert_eq!(decorated.challenge, control.challenge);
        assert_eq!(decorated.visual_seed(), control.visual_seed());
    }

    #[test]
    fn vendor_catalogue_and_malformed_settings_are_validated() {
        let c = Config::sample().unwrap();
        assert!(c.habitat.themes.len() >= 8 && c.habitat.items.len() >= 12);
        assert!(c.habitat.items.iter().any(|i| i.price == 3));
        assert!(c.habitat.items.iter().any(|i| i.price == 5));
        for case in 0..13 {
            let mut rules = c.habitat.clone();
            match case {
                0 => rules.themes.clear(),
                1 => rules.items.clear(),
                2 => rules.themes[0].id = 1,
                3 => rules.themes[0].wall = 16,
                4 => rules.themes[0].pattern = 8,
                5 => rules.items[0].id = 64,
                6 => rules.items[0].slot = 16,
                7 => rules.items[0].kind = 8,
                8 => rules.items[0].price = crate::game::MAX_COINS + 1,
                9 => rules.items[0].reward_wins = 1,
                10 => rules.items[0].name = "bad\nname".into(),
                11 => rules.items[0].price = 0,
                _ => rules.items[0].reward_age = 1_000_001,
            }
            assert!(rules.validate().is_err(), "case {case}");
        }
        for source in [
            "[mons.habitat]\nthemes=[]",
            "[mons.habitat]\nunknown=true",
            "[mons.habitat]\nitems='wrong'",
        ] {
            assert!(Config::from_value(&source.parse::<toml::Value>().unwrap()).is_err());
        }
    }

    #[test]
    fn purchases_and_unowned_placement_fail_without_mutation() {
        let c = Config::sample().unwrap();
        let mut game = Game::new(&c.game, 7);
        let original = game.clone();
        assert!(game.toggle_item(0, &c.habitat).is_err());
        assert!(game.buy_item(255, &c.habitat).is_err());
        assert!(game.set_theme(255, &c.habitat).is_err());
        assert!(game.buy_item(10, &c.habitat).is_err());
        assert_eq!(game, original);
        game.coins = 2;
        let poor = game.clone();
        assert!(game.buy_item(0, &c.habitat).is_err());
        assert_eq!(game, poor);
        game.coins = c.game.starting_coins;
        game.buy_item(0, &c.habitat).unwrap();
        assert_eq!(game.coins, c.game.starting_coins - 3);
        assert_eq!(game.habitat.owned, 1);
        assert_eq!(game.habitat.placed, 0);
        let bought = game.clone();
        assert!(game.buy_item(0, &c.habitat).is_err());
        assert_eq!(game, bought);
        game.toggle_item(0, &c.habitat).unwrap();
        assert_eq!(game.coins, bought.coins);
        assert_eq!(game.habitat.placed, 1);
        game.toggle_item(0, &c.habitat).unwrap();
        assert_eq!(game, bought);
        game.coins = crate::game::MAX_COINS + 1;
        let invalid = game.clone();
        assert!(game.buy_item(1, &c.habitat).is_err());
        assert_eq!(game, invalid);
    }

    fn win(game: &mut Game, rules: &crate::game::Rules) {
        game.care(Care::Play, rules);
        let pattern = game.challenge.as_ref().unwrap().pattern;
        for _ in 0..rules.memory_reveal_ticks {
            game.tick(rules);
        }
        for direction in pattern {
            game.guess(direction, rules);
        }
    }

    #[test]
    fn earned_items_follow_real_wins_and_growth_and_never_become_purchases() {
        let mut c = Config::sample().unwrap();
        c.game.need_decay = 1;
        let mut game = Game::new(&c.game, 7);
        assert!(!game.habitat.owns(10, game.wins, game.age_ticks, &c.habitat));
        assert!(!game.habitat.owns(12, game.wins, game.age_ticks, &c.habitat));
        for _ in 0..c.game.hatch_ticks {
            game.tick(&c.game);
        }
        win(&mut game, &c.game);
        assert_eq!(game.wins, 1);
        let before = game.coins;
        game.toggle_item(10, &c.habitat).unwrap();
        assert_eq!(game.coins, before);
        assert_eq!(game.habitat.owned, 0);
        assert!(!game.habitat.owns(11, game.wins, game.age_ticks, &c.habitat));
        win(&mut game, &c.game);
        win(&mut game, &c.game);
        assert_eq!(game.wins, 3);
        game.toggle_item(11, &c.habitat).unwrap();
        assert_eq!(game.habitat.placed & bit(10), 0);
        assert_ne!(game.habitat.placed & bit(11), 0);
        while game.age_ticks < c.game.child_ticks {
            game.tick(&c.game);
        }
        assert!(game.habitat.owns(12, game.wins, game.age_ticks, &c.habitat));
        assert!(!game.habitat.owns(13, game.wins, game.age_ticks, &c.habitat));
        game.toggle_item(12, &c.habitat).unwrap();
        while game.age_ticks < c.game.adult_ticks {
            if game.fullness < 60 {
                game.care(Care::Feed, &c.game);
            }
            game.care(Care::Clean, &c.game);
            if game.energy < 60 && !game.sleeping {
                game.care(Care::Rest, &c.game);
            }
            game.tick(&c.game);
        }
        assert!(game.habitat.owns(13, game.wins, game.age_ticks, &c.habitat));
        assert_eq!(game.habitat.owned, 0);
        let frozen = game.clone();
        for _ in 0..100 {
            game.habitat.layout(0);
        }
        assert_eq!(game, frozen);
        let mut both = c.habitat.items[10].clone();
        both.reward_age = c.game.adult_ticks;
        assert!(!both.eligible(1, c.game.child_ticks));
        assert!(!both.eligible(0, c.game.adult_ticks));
        assert!(both.eligible(1, c.game.adult_ticks));
    }

    #[test]
    fn invalid_habitat_state_and_forged_rewards_are_rejected() {
        let c = Config::sample().unwrap();
        let original = Game::new(&c.game, 7);
        for case in 0..7 {
            let mut game = original.clone();
            match case {
                0 => game.habitat.seed = 0,
                1 => game.habitat.theme = 255,
                2 => game.habitat.owned = 1 << 63,
                3 => game.habitat.placed = 1,
                4 => game.habitat.owned = 1 << 10,
                5 => game.habitat.placed = 1 << 10,
                _ => game.habitat.placed = 1 << 63,
            }
            assert!(game.validate().is_err(), "case {case}");
            let before = game.clone();
            assert!(game.buy_item(0, &c.habitat).is_err());
            assert_eq!(game, before);
        }
        let mut game = original;
        game.wins = 3;
        game.habitat.placed = bit(10) | bit(11);
        assert!(game.validate().is_err());
        // The full 64-item bitset is supported without an overflowing shift.
        let mut rules = c.habitat.clone();
        while rules.items.len() < 64 {
            let mut item = rules.items[0].clone();
            item.id = rules.items.len() as u8;
            rules.items.push(item);
        }
        rules.validate().unwrap();
        game.habitat.placed = 0;
        game.buy_item(63, &rules).unwrap();
        game.toggle_item(63, &rules).unwrap();
        assert_eq!(game.habitat.owned, 1 << 63);
        game.validate_habitat(&rules).unwrap();
    }
}
