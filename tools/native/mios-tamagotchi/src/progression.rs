// AI-hint: Original generated Mon names, independent hatch genetics and bounded care-funded training.
// AI-related: game.rs, save.rs, config.rs, usr/share/mios/mios.toml [tamagotchi.game.training]
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

const MAX_XP: u32 = 1_000_000;
pub(crate) const MAX_TRAINING: u32 = 1_000_000;

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TrainingRules {
    pub name_starts: Vec<String>,
    pub name_ends: Vec<String>,
    pub species_colors: Vec<u8>,
    pub personality_colors: BTreeMap<String, u8>,
    pub hatch_min: u16,
    pub hatch_max: u16,
    pub hatch_bonus: u16,
    pub max_level: u8,
    pub xp_base: u32,
    pub xp_per_level: u32,
    pub xp_gain_min: u32,
    pub xp_gain_max: u32,
    pub energy_cost: u8,
    pub fullness_cost: u8,
    pub happiness_gain: u8,
    pub cooldown_ticks: u64,
    pub level_coin_reward: u32,
}

impl TrainingRules {
    pub fn validate(&self) -> Result<(), String> {
        for pool in [&self.name_starts, &self.name_ends] {
            if !(1..=64).contains(&pool.len())
                || pool.iter().any(|s| {
                    !(1..=6).contains(&s.len()) || !s.bytes().all(|b| b.is_ascii_alphabetic())
                })
            {
                return Err("tamagotchi.game.training: name pools require 1..=64 ASCII letter syllables of 1..=6 bytes".into());
            }
        }
        let keys = [
            "cozy",
            "athletic",
            "grumbly",
            "showy",
            "neighborly",
            "sunny",
            "refined",
            "protective",
        ];
        if self.species_colors.len() != 8
            || self.species_colors.iter().any(|c| *c > 15)
            || self.personality_colors.len() != keys.len()
            || keys
                .iter()
                .any(|key| !self.personality_colors.contains_key(*key))
            || self.personality_colors.values().any(|c| *c > 15)
            || self
                .personality_colors
                .values()
                .collect::<BTreeSet<_>>()
                .len()
                != 8
        {
            return Err("tamagotchi.game.training: require eight species colors and eight distinct personality colors in 0..=15 for the actual archetypes".into());
        }
        if self.hatch_min == 0
            || self.hatch_min > self.hatch_max
            || self.hatch_max > 100
            || self.hatch_bonus > 20
            || !(1..=99).contains(&self.max_level)
            || !(1..=10_000).contains(&self.xp_base)
            || self.xp_per_level > 10_000
            || self.xp_gain_min == 0
            || self.xp_gain_min > self.xp_gain_max
            || self.xp_gain_max > 10_000
            || !(1..=100).contains(&self.energy_cost)
            || !(1..=100).contains(&self.fullness_cost)
            || self.happiness_gain > 100
            || !(1..=1_000_000).contains(&self.cooldown_ticks)
            || self.level_coin_reward > 1_000
        {
            return Err("tamagotchi.game.training: invalid hatch, level, XP, care, cooldown or reward bounds".into());
        }
        Ok(())
    }

    pub fn label(index: usize) -> &'static str {
        ["Swim", "Flight", "Run", "Power", "Stamina"]
            .get(index)
            .copied()
            .unwrap_or("Unknown")
    }

    pub fn palette(&self, shape: u8, archetype: &str) -> Result<(u8, u8), String> {
        let body = self
            .species_colors
            .get(usize::from(shape % 8))
            .ok_or("missing species palette")?;
        let accent = self
            .personality_colors
            .get(archetype)
            .ok_or_else(|| format!("missing personality palette for {archetype}"))?;
        Ok((*body, *accent))
    }

    pub fn xp_needed(&self, level: u8) -> u32 {
        self.xp_base
            .saturating_add(self.xp_per_level.saturating_mul(u32::from(level)))
    }
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Stat {
    pub value: u16,
    pub level: u8,
    pub xp: u32,
    pub aptitude: u8,
}
impl Stat {
    pub fn grade(&self) -> &'static str {
        ["E", "D", "C", "B", "A"]
            .get(usize::from(self.aptitude))
            .copied()
            .unwrap_or("?")
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Progress {
    pub name: String,
    pub hatched: bool,
    pub legacy: bool,
    pub origin_seed: u64,
    pub rng: u64,
    pub stats: [Stat; 5],
    pub total_training: u32,
    pub last_train_tick: Option<u64>,
}

impl Default for Progress {
    // Deserialization compatibility only. The save loader seeds legacy pets
    // from their own saved visual seed before validating or preparing them.
    fn default() -> Self {
        Self::legacy(0)
    }
}

impl Progress {
    pub fn new(creation_seed: u64) -> Self {
        // Separate domain and avalanche nearby creation seeds; never advance Game::rng.
        let mut seed = creation_seed ^ 0x6d6f_6e73_6861_7463;
        seed = (seed ^ (seed >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
        seed = (seed ^ (seed >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
        seed = (seed ^ (seed >> 31)).max(1);
        Self {
            name: String::new(),
            hatched: false,
            legacy: false,
            origin_seed: seed,
            rng: seed,
            stats: [Stat::default(); 5],
            total_training: 0,
            last_train_tick: None,
        }
    }

    pub fn birth_preview(seed: u64, rules: &TrainingRules) -> Self {
        let mut birth = Self::new(seed);
        birth.hatch(rules);
        birth
    }

    pub fn legacy(seed: u64) -> Self {
        Self {
            legacy: true,
            ..Self::new(seed)
        }
    }

    pub fn validate(&self) -> Result<(), String> {
        let name_valid =
            self.name.len() <= 16 && self.name.bytes().all(|b| b.is_ascii_alphabetic());
        if self.origin_seed == 0
            || self.rng == 0
            || !name_valid
            || self.total_training > MAX_TRAINING
            || (self.total_training == 0) != self.last_train_tick.is_none()
            || self
                .stats
                .iter()
                .any(|s| s.level > 99 || s.value > 100 || s.xp > MAX_XP || s.aptitude > 4)
            || (self.hatched
                && (self.stats.iter().any(|s| s.value == 0)
                    || (self.legacy && !self.name.is_empty())
                    || (!self.legacy && self.name.is_empty())))
            || (!self.hatched
                && (!self.name.is_empty()
                    || self.stats != [Stat::default(); 5]
                    || self.total_training != 0
                    || self.rng != self.origin_seed))
            || (self.total_training == 0 && self.stats.iter().any(|s| s.level != 0 || s.xp != 0))
        {
            return Err(
                "save contains invalid progression seed, name, stats or training counters".into(),
            );
        }
        Ok(())
    }

    fn draw(&mut self, count: u32) -> u32 {
        // Nonzero xorshift state is saved independently from the memory game.
        self.rng ^= self.rng << 13;
        self.rng ^= self.rng >> 7;
        self.rng ^= self.rng << 17;
        ((self.rng >> 32) % u64::from(count)) as u32
    }

    fn hatch(&mut self, rules: &TrainingRules) {
        if self.hatched {
            return;
        }
        for i in 0..self.stats.len() {
            let value = rules.hatch_min
                + self.draw(u32::from(rules.hatch_max - rules.hatch_min) + 1) as u16;
            let aptitude = self.draw(5) as u8;
            self.stats[i] = Stat {
                value,
                aptitude,
                ..Stat::default()
            };
        }
        if !self.legacy && rules.hatch_bonus > 0 {
            let stat = &mut self.stats[crate::genetics::bonus_index(self.origin_seed)];
            stat.value = stat.value.saturating_add(rules.hatch_bonus).min(100);
            stat.aptitude = stat.aptitude.saturating_add(1).min(4);
        }
        if !self.legacy {
            let start = self.draw(rules.name_starts.len() as u32) as usize;
            let end = self.draw(rules.name_ends.len() as u32) as usize;
            self.name = format!("{}{}", rules.name_starts[start], rules.name_ends[end])
                .to_ascii_lowercase();
            self.name[..1].make_ascii_uppercase();
        }
        self.hatched = true;
    }
}

impl crate::game::Game {
    pub fn prepare_progress(&mut self, rules: &crate::game::Rules) -> Result<(), String> {
        rules.training.validate()?;
        self.progress.validate()?;
        if self.age_ticks >= rules.hatch_ticks && !self.progress.hatched {
            self.progress.hatch(&rules.training);
        }
        Ok(())
    }

    pub fn train(&mut self, index: usize, rules: &crate::game::Rules) -> Result<String, String> {
        use crate::game::Stage;
        let r = &rules.training;
        r.validate()?;
        self.progress.validate()?;
        let refuse = if index >= 5 {
            Some("Choose one of the five training activities.")
        } else if self.stage(rules) == Stage::Egg {
            Some("Wait for your egg to hatch before training.")
        } else if self.health == 0 {
            Some("Your pet has passed away. Choose New pet to begin again.")
        } else if self.paused {
            Some("Resume the clock before training.")
        } else if self.sleeping {
            Some("Wake your pet before training.")
        } else if self.ill {
            Some("Treat your pet's illness before training.")
        } else if self.challenge.is_some() {
            Some("Finish or cancel the memory game before training.")
        } else if !self.progress.hatched {
            Some("Prepare your pet's hatch stats before training.")
        } else if self.progress.stats[index].level >= r.max_level {
            Some("This skill has reached its training level cap.")
        } else if self.energy < r.energy_cost || self.fullness < r.fullness_cost {
            Some("Your pet needs food and rest before training.")
        } else if self.progress.total_training >= MAX_TRAINING {
            Some("This pet has reached the training session limit.")
        } else if self
            .progress
            .last_train_tick
            .is_some_and(|last| self.age_ticks.saturating_sub(last) < r.cooldown_ticks)
        {
            Some("Training cooldown: leave the menu and let active game time pass.")
        } else {
            None
        };
        if let Some(message) = refuse {
            return Err(message.into());
        }
        // Stage all progression changes, including RNG, before committing costs.
        let mut progress = self.progress.clone();
        let gain = r.xp_gain_min
            + progress.draw(r.xp_gain_max - r.xp_gain_min + 1)
            + u32::from(progress.stats[index].aptitude);
        let stat = &mut progress.stats[index];
        stat.xp = stat.xp.saturating_add(gain);
        let mut levels = 0u32;
        while stat.level < r.max_level && stat.xp >= r.xp_needed(stat.level) {
            stat.xp -= r.xp_needed(stat.level);
            stat.level += 1;
            levels += 1;
        }
        if stat.xp > MAX_XP {
            return Err("Training XP would exceed the saved stat limit.".into());
        }
        // Real earned levels train the hatch stat as well as its level counter.
        // Growth follows this session's configured XP and innate aptitude.
        let growth = (gain / r.xp_base).max(1) + u32::from(stat.aptitude);
        stat.value =
            (u32::from(stat.value).saturating_add(levels.saturating_mul(growth))).min(100) as u16;
        self.energy -= r.energy_cost;
        self.fullness -= r.fullness_cost;
        self.happiness = self.happiness.saturating_add(r.happiness_gain).min(100);
        self.coins = self
            .coins
            .saturating_add(levels.saturating_mul(r.level_coin_reward))
            .min(crate::game::MAX_COINS);
        let level = stat.level;
        progress.total_training += 1;
        progress.last_train_tick = Some(self.age_ticks);
        self.progress = progress;
        Ok(format!(
            "{} training: +{} XP, level {} ({} level gains).",
            TrainingRules::label(index),
            gain,
            level,
            levels
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        config::Config,
        game::{Challenge, Direction, Game},
    };

    fn hatched() -> (Game, crate::game::Rules) {
        let r = Config::sample().unwrap().game;
        let mut g = Game::new(&r, 109);
        for _ in 0..r.hatch_ticks {
            g.tick(&r);
        }
        (g, r)
    }

    #[test]
    fn hatch_is_once_and_names_are_stable_diverse_and_independent_of_memory() {
        let r = Config::sample().unwrap().game;
        let mut names = BTreeSet::new();
        let mut genetics = BTreeSet::new();
        for seed in 0..128 {
            let mut a = Game::new(&r, seed);
            let mut b = a.clone();
            let egg = a.clone();
            a.prepare_progress(&r).unwrap();
            assert_eq!(a, egg);
            assert_eq!(a.progress.stats, [Stat::default(); 5]);
            assert!(a.progress.name.is_empty());
            for _ in 0..r.hatch_ticks {
                a.tick(&r);
                b.tick(&r);
            }
            assert_eq!(a, b);
            assert_eq!(a.visual_seed(), seed);
            assert!(a.progress.hatched);
            assert!(a.progress.stats.iter().all(|s| {
                (r.training.hatch_min
                    ..=r.training
                        .hatch_max
                        .saturating_add(r.training.hatch_bonus)
                        .min(100))
                    .contains(&s.value)
                    && s.aptitude <= 4
            }));
            let once = a.clone();
            a.prepare_progress(&r).unwrap();
            assert_eq!(a, once);
            names.insert(a.progress.name.clone());
            genetics.insert(format!("{:?}", a.progress.stats));
        }
        assert!(names.len() >= 16);
        assert!(genetics.len() >= 100);
    }

    #[test]
    fn palettes_bind_species_and_actual_personality() {
        let c = Config::sample().unwrap();
        for character in &c.characters {
            let (body, accent) = c
                .game
                .training
                .palette(character.shape, &character.archetype)
                .unwrap();
            assert_eq!(
                body,
                c.game.training.species_colors[usize::from(character.shape % 8)]
            );
            assert_eq!(
                accent,
                c.game.training.personality_colors[&character.archetype]
            );
            assert_eq!(
                body,
                c.game
                    .training
                    .palette(character.shape % 8, &character.archetype)
                    .unwrap()
                    .0
            );
        }
        assert!(c.game.training.palette(0, "unknown").is_err());
        let mut bad = c.game.training;
        bad.species_colors.pop();
        assert!(bad.validate().is_err());
    }

    #[test]
    fn training_levels_rewards_costs_and_leftovers_are_real_and_bounded() {
        let (mut g, mut r) = hatched();
        r.training.xp_base = 10;
        r.training.xp_per_level = 2;
        r.training.xp_gain_min = 25;
        r.training.xp_gain_max = 25;
        g.progress.stats[0].aptitude = 0;
        let before = g.clone();
        g.train(0, &r).unwrap();
        assert_eq!(g.progress.stats[0].level, 2);
        assert_eq!(g.progress.stats[0].xp, 3);
        assert_eq!(
            g.progress.stats[0].value,
            (before.progress.stats[0].value + 4).min(100)
        );
        assert_eq!(g.coins, before.coins + 2 * r.training.level_coin_reward);
        assert_eq!(g.fullness, before.fullness - r.training.fullness_cost);
        assert_eq!(g.energy, before.energy - r.training.energy_cost);
        assert_eq!(
            g.happiness,
            before
                .happiness
                .saturating_add(r.training.happiness_gain)
                .min(100)
        );
        assert_eq!(g.progress.total_training, 1);
        assert_eq!(g.progress.last_train_tick, Some(g.age_ticks));
        assert_eq!(g.visual_seed(), before.visual_seed());
        let after = g.clone();
        assert!(g.train(0, &r).unwrap_err().contains("cooldown"));
        assert_eq!(g, after);
        g.tick(&r);
        g.train(0, &r).unwrap();
        assert_eq!(g.progress.total_training, 2);
        g.coins = crate::game::MAX_COINS;
        g.age_ticks += r.training.cooldown_ticks;
        g.train(1, &r).unwrap();
        assert_eq!(g.coins, crate::game::MAX_COINS);
    }

    #[test]
    fn every_training_refusal_is_fully_nonmutating_including_both_rngs() {
        let (g, r) = hatched();
        for case in 0..12 {
            let mut pet = g.clone();
            let index = if case == 0 { 5 } else { 0 };
            match case {
                1 => pet.age_ticks = 0,
                2 => pet.health = 0,
                3 => pet.paused = true,
                4 => pet.sleeping = true,
                5 => pet.ill = true,
                6 => {
                    pet.challenge = Some(Challenge {
                        pattern: [Direction::Up; 3],
                        entered: 0,
                        reveal: 1,
                    })
                }
                7 => {
                    pet.progress.stats[0].level = r.training.max_level;
                    pet.progress.total_training = 1;
                    pet.progress.last_train_tick = Some(pet.age_ticks - 1);
                }
                8 => pet.energy = r.training.energy_cost - 1,
                9 => pet.fullness = r.training.fullness_cost - 1,
                10 => {
                    pet.progress.total_training = 1;
                    pet.progress.last_train_tick = Some(pet.age_ticks);
                }
                11 => pet.progress = Progress::new(19),
                _ => {}
            }
            let before = pet.clone();
            let error = pet.train(index, &r).unwrap_err();
            if case == 7 {
                assert!(error.contains("level cap"));
            }
            assert_eq!(pet, before, "case {case}");
        }
        let mut egg = Game::new(&r, 19);
        let before = egg.clone();
        assert!(egg.train(0, &r).is_err());
        assert_eq!(egg, before);
    }

    #[test]
    fn pause_and_legacy_preparation_do_not_apply_time_or_mutate_original_fields() {
        let (mut pet, r) = hatched();
        pet.paused = true;
        let paused = pet.clone();
        for _ in 0..100 {
            pet.tick(&r);
        }
        assert_eq!(pet, paused);
        pet.progress = Progress::legacy(pet.visual_seed());
        let original = serde_json::to_value(&pet).unwrap();
        pet.prepare_progress(&r).unwrap();
        assert!(pet.progress.hatched);
        assert!(pet.progress.legacy);
        assert!(pet.progress.name.is_empty());
        let mut after = serde_json::to_value(&pet).unwrap();
        let mut before = original;
        after.as_object_mut().unwrap().remove("progress");
        before.as_object_mut().unwrap().remove("progress");
        assert_eq!(after, before);
    }

    #[test]
    fn malformed_training_and_progress_are_rejected() {
        let r = Config::sample().unwrap().game.training;
        for case in 0..13 {
            let mut bad = r.clone();
            match case {
                0 => bad.name_starts.clear(),
                1 => bad.name_ends[0] = "\u{1b}x".into(),
                2 => bad.name_starts[0] = "toolong".into(),
                3 => bad.species_colors[0] = 16,
                4 => {
                    bad.personality_colors.remove("cozy");
                }
                5 => {
                    bad.personality_colors
                        .insert("cozy".into(), bad.personality_colors["sunny"]);
                }
                6 => bad.hatch_min = 0,
                7 => bad.hatch_max = 101,
                8 => bad.max_level = 100,
                9 => bad.xp_base = 0,
                10 => bad.xp_gain_min = bad.xp_gain_max + 1,
                11 => bad.cooldown_ticks = 0,
                _ => bad.energy_cost = 0,
            }
            assert!(bad.validate().is_err(), "case {case}");
        }
        let (pet, _) = hatched();
        for case in 0..10 {
            let mut bad = pet.progress.clone();
            match case {
                0 => bad.origin_seed = 0,
                1 => bad.rng = 0,
                2 => bad.name = "not a name".into(),
                3 => bad.stats[0].value = 101,
                4 => bad.stats[0].level = 100,
                5 => bad.stats[0].xp = MAX_XP + 1,
                6 => bad.stats[0].aptitude = 5,
                7 => bad.total_training = MAX_TRAINING + 1,
                8 => bad.last_train_tick = Some(3),
                _ => bad.hatched = false,
            }
            assert!(bad.validate().is_err(), "case {case}");
        }
    }
}

#[cfg(test)]
mod aptitude_tests {
    use crate::{config::Config, game::Game};

    #[test]
    fn aptitude_changes_real_xp_and_level_cap_preserves_leftovers() {
        let mut r = Config::sample().unwrap().game;
        let mut first = Game::new(&r, 51);
        for _ in 0..r.hatch_ticks {
            first.tick(&r);
        }
        let mut second = first.clone();
        first.progress.stats[0].aptitude = 0;
        second.progress.stats[0].aptitude = 4;
        r.training.xp_base = 10_000;
        r.training.xp_gain_min = 6;
        r.training.xp_gain_max = 6;
        first.train(0, &r).unwrap();
        second.train(0, &r).unwrap();
        assert_eq!(first.progress.stats[0].xp, 6);
        assert_eq!(second.progress.stats[0].xp, 10);
        assert_eq!(first.progress.rng, second.progress.rng);
        assert_eq!(
            first.progress.stats[0].value,
            second.progress.stats[0].value
        );
        assert_eq!(first.progress.stats[0].grade(), "E");
        assert_eq!(second.progress.stats[0].grade(), "A");
        first.tick(&r);
        r.training.max_level = 1;
        r.training.xp_base = 10;
        r.training.xp_gain_min = 25;
        r.training.xp_gain_max = 25;
        let before_level = first.progress.stats[0].value;
        first.train(0, &r).unwrap();
        assert_eq!(first.progress.stats[0].value, (before_level + 2).min(100));
        assert_eq!(first.progress.stats[0].level, 1);
        assert_eq!(first.progress.stats[0].xp, 21);
        second.tick(&r);
        second.progress.stats[0].value = 99;
        second.train(0, &r).unwrap();
        assert_eq!(second.progress.stats[0].value, 100);
        second.progress.validate().unwrap();
        let capped = first.clone();
        assert!(first.train(0, &r).unwrap_err().contains("level cap"));
        assert_eq!(first, capped);
        let mut bad = r;
        bad.training.cooldown_ticks = 0;
        assert!(first.train(1, &bad).is_err());
        assert_eq!(first, capped);
    }
}

#[cfg(test)]
mod xp_boundary_tests {
    use crate::{config::Config, game::Game};

    #[test]
    fn xp_above_safety_limit_before_level_preserves_exact_valid_leftover() {
        let mut r = Config::sample().unwrap().game;
        let mut game = Game::new(&r, 118);
        for _ in 0..r.hatch_ticks {
            game.tick(&r);
        }
        r.training.xp_base = 10_000;
        r.training.xp_per_level = 10_000;
        r.training.xp_gain_min = 10_000;
        r.training.xp_gain_max = 10_000;
        game.progress.total_training = 99;
        game.progress.last_train_tick = Some(game.age_ticks - 1);
        game.progress.stats[0].level = 98;
        game.progress.stats[0].xp = 989_999;
        game.progress.stats[0].aptitude = 4;
        let memory_rng = game.visual_seed();
        game.train(0, &r).unwrap();
        assert_eq!(game.progress.stats[0].level, 99);
        assert_eq!(game.progress.stats[0].xp, 10_003);
        assert_eq!(game.visual_seed(), memory_rng);
        game.progress.validate().unwrap();
    }

    #[test]
    fn excessive_remaining_xp_refuses_without_rng_cost_or_state_mutation() {
        let mut r = Config::sample().unwrap().game;
        let mut game = Game::new(&r, 118);
        for _ in 0..r.hatch_ticks {
            game.tick(&r);
        }
        r.training.max_level = 1;
        r.training.xp_base = 1;
        r.training.xp_per_level = 0;
        r.training.xp_gain_min = 10_000;
        r.training.xp_gain_max = 10_000;
        game.progress.total_training = 1;
        game.progress.last_train_tick = Some(game.age_ticks - 1);
        game.progress.stats[0].xp = 1_000_000;
        game.progress.stats[0].aptitude = 4;
        game.progress.validate().unwrap();
        let before = game.clone();
        assert!(game.train(0, &r).unwrap_err().contains("stat limit"));
        assert_eq!(game, before);
    }
}
