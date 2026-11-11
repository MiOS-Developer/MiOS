// AI-hint: Deterministic virtual-pet care, accelerated growth and a three-arrow memory game; no wall-clock catch-up.
// AI-related: config.rs, save.rs, ui.rs, usr/share/mios/mios.toml [tamagotchi.game]
use serde::{Deserialize, Serialize};

pub const MAX_COINS: u32 = 1_000_000;

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Rules {
    pub tick_seconds: u64,
    pub hatch_ticks: u64,
    pub child_ticks: u64,
    pub adult_ticks: u64,
    pub need_decay: u8,
    pub initial_need: u8,
    pub illness_threshold: u8,
    pub healthy_threshold: u8,
    pub healthy_regen: u8,
    pub health_loss: u8,
    pub care_gain: u8,
    pub play_reward: u32,
    pub feed_cost: u32,
    pub medicine_cost: u32,
    pub starting_coins: u32,
    pub memory_reveal_ticks: u16,
}

impl Rules {
    pub fn validate(&self) -> Result<(), String> {
        if !(1..=60).contains(&self.tick_seconds)
            || self.hatch_ticks == 0
            || self.hatch_ticks >= self.child_ticks
            || self.child_ticks >= self.adult_ticks
            || self.adult_ticks > 1_000_000
        {
            return Err("tamagotchi.game: tick_seconds must be 1..=60 and growth ticks must satisfy 0 < hatch < child < adult <= 1000000".into());
        }
        if self.initial_need > 100
            || self.illness_threshold >= 100
            || !(1..=100).contains(&self.healthy_threshold)
            || self.healthy_regen > 100
            || !(1..=20).contains(&self.need_decay)
            || !(1..=100).contains(&self.health_loss)
            || !(1..=100).contains(&self.care_gain)
            || !(1..=30).contains(&self.memory_reveal_ticks)
            || [
                self.play_reward,
                self.feed_cost,
                self.medicine_cost,
                self.starting_coins,
            ]
            .iter()
            .any(|n| *n > MAX_COINS)
        {
            return Err("tamagotchi.game: invalid care, memory or currency limits".into());
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Stage {
    Egg,
    Baby,
    Child,
    Adult,
    Dead,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum Direction {
    Up,
    Down,
    Left,
    Right,
}
impl Direction {
    pub fn label(self) -> &'static str {
        match self {
            Self::Up => "UP",
            Self::Down => "DOWN",
            Self::Left => "LEFT",
            Self::Right => "RIGHT",
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Challenge {
    pub pattern: [Direction; 3],
    pub entered: u8,
    pub reveal: u16,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Game {
    #[serde(default)]
    pub character: u8,
    #[serde(default)]
    pub habitat: crate::habitat::Habitat,
    pub age_ticks: u64,
    pub fullness: u8,
    pub happiness: u8,
    pub hygiene: u8,
    pub energy: u8,
    pub health: u8,
    pub coins: u32,
    pub wins: u32,
    pub paused: bool,
    pub sleeping: bool,
    pub ill: bool,
    pub challenge: Option<Challenge>,
    rng: u64,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Care {
    Feed,
    Play,
    Clean,
    Rest,
    Medicine,
}

impl Game {
    /// A read-only seed for cosmetic simulation. Presentation keeps its own RNG
    /// so animation cadence cannot change memory answers or saved gameplay state.
    pub fn visual_seed(&self) -> u64 {
        self.rng
    }
    pub fn new(rules: &Rules, seed: u64) -> Self {
        Self {
            // Select an animal family, then one of its two original individuals.
            // This does not consume the memory game's saved RNG stream.
            character: ((seed % 8) + ((seed >> 8) % 2) * 8) as u8,
            habitat: crate::habitat::Habitat::new(seed),
            age_ticks: 0,
            fullness: rules.initial_need,
            happiness: rules.initial_need,
            hygiene: 100,
            energy: 100,
            health: 100,
            coins: rules.starting_coins,
            wins: 0,
            paused: false,
            sleeping: false,
            ill: false,
            challenge: None,
            rng: seed,
        }
    }
    pub fn validate(&self) -> Result<(), String> {
        self.validate_habitat(crate::habitat::vendor_rules()?)
    }
    pub fn validate_habitat(&self, habitat: &crate::habitat::HabitatRules) -> Result<(), String> {
        self.habitat.validate(self.wins, self.age_ticks, habitat)?;
        if [
            self.fullness,
            self.happiness,
            self.hygiene,
            self.energy,
            self.health,
        ]
        .iter()
        .any(|n| *n > 100)
            || self.character >= 64
            || self.coins > MAX_COINS
            || ((self.health == 0 || self.sleeping) && self.challenge.is_some())
            || self.challenge.as_ref().is_some_and(|c| {
                c.entered >= 3 || c.reveal > 30 || (c.reveal > 0 && c.entered != 0)
            })
        {
            return Err("save contains invalid pet needs, currency or memory progress".into());
        }
        Ok(())
    }
    pub fn stage(&self, r: &Rules) -> Stage {
        if self.health == 0 {
            Stage::Dead
        } else if self.age_ticks < r.hatch_ticks {
            Stage::Egg
        } else if self.age_ticks < r.child_ticks {
            Stage::Baby
        } else if self.age_ticks < r.adult_ticks {
            Stage::Child
        } else {
            Stage::Adult
        }
    }
    pub fn thriving(&self, r: &Rules) -> bool {
        self.stage(r) == Stage::Adult
            && !self.ill
            && self.health >= r.healthy_threshold
            && self.happiness >= r.healthy_threshold
    }
    pub fn tick(&mut self, r: &Rules) {
        if self.paused || self.health == 0 {
            return;
        }
        self.age_ticks = self.age_ticks.saturating_add(1);
        if self.stage(r) == Stage::Egg {
            return;
        }
        self.fullness = self.fullness.saturating_sub(r.need_decay);
        self.happiness = self.happiness.saturating_sub(r.need_decay);
        self.hygiene = self.hygiene.saturating_sub(r.need_decay);
        self.energy = if self.sleeping {
            self.energy.saturating_add(r.care_gain).min(100)
        } else {
            self.energy.saturating_sub(r.need_decay)
        };
        if self.hygiene <= r.illness_threshold {
            self.ill = true;
        }
        if self.ill || [self.fullness, self.happiness, self.hygiene, self.energy].contains(&0) {
            self.health = self.health.saturating_sub(r.health_loss);
        } else if [self.fullness, self.happiness, self.hygiene, self.energy]
            .iter()
            .all(|n| *n >= r.healthy_threshold)
        {
            self.health = self.health.saturating_add(r.healthy_regen).min(100);
        }
        if self.health == 0 {
            self.challenge = None;
        }
        if let Some(c) = &mut self.challenge {
            c.reveal = c.reveal.saturating_sub(1);
        }
    }
    pub fn care(&mut self, care: Care, r: &Rules) -> &'static str {
        if self.paused {
            return "Resume the clock before caring for your pet.";
        }
        if self.health == 0 {
            return "Your pet has passed away. Choose New pet to begin again.";
        }
        if self.stage(r) == Stage::Egg {
            return "The egg will hatch soon. Keep it company!";
        }
        if self.challenge.is_some() {
            return "Finish the memory game, or Escape to cancel it.";
        }
        match care {
            Care::Feed if self.fullness == 100 => "Your pet is already full.",
            Care::Feed if self.coins < r.feed_cost => {
                "Not enough coins. Win a memory game to earn some."
            }
            Care::Feed => {
                self.coins -= r.feed_cost;
                self.fullness = self.fullness.saturating_add(r.care_gain).min(100);
                self.sleeping = false;
                "A tasty meal!"
            }
            Care::Clean => {
                self.hygiene = 100;
                "All clean! Medicine treats any existing illness."
            }
            Care::Rest => {
                self.sleeping = !self.sleeping;
                if self.sleeping {
                    "Resting: energy recovers while the clock runs."
                } else {
                    "Awake again!"
                }
            }
            Care::Medicine if !self.ill && self.health == 100 => "No medicine needed right now.",
            Care::Medicine if self.coins < r.medicine_cost => {
                "Not enough coins for medicine. Play for more."
            }
            Care::Medicine => {
                self.coins -= r.medicine_cost;
                self.ill = false;
                self.health = self.health.saturating_add(r.care_gain).min(100);
                "Feeling better! Keep the room clean."
            }
            Care::Play if self.energy < r.need_decay => "Too tired to play. Rest first.",
            Care::Play => {
                self.sleeping = false;
                let mut pattern = [Direction::Up; 3];
                for d in &mut pattern {
                    // Reproducible PRNG state is saved; this is a local memory game, not cryptography.
                    self.rng = self.rng.wrapping_mul(6364136223846793005).wrapping_add(1);
                    *d = [
                        Direction::Up,
                        Direction::Down,
                        Direction::Left,
                        Direction::Right,
                    ][(self.rng >> 32) as usize % 4];
                }
                self.challenge = Some(Challenge {
                    pattern,
                    entered: 0,
                    reveal: r.memory_reveal_ticks,
                });
                "Remember the arrows. Repeat them after the pattern disappears."
            }
        }
    }
    pub fn guess(&mut self, direction: Direction, r: &Rules) -> &'static str {
        if self.paused {
            return "Resume the clock to continue the game.";
        }
        let Some(c) = &mut self.challenge else {
            return "Choose Play to start a memory game.";
        };
        if c.reveal > 0 {
            return "Watch first; wait until the pattern disappears.";
        }
        if c.pattern.get(usize::from(c.entered)) != Some(&direction) {
            self.challenge = None;
            self.happiness = self.happiness.saturating_sub(r.need_decay);
            return "That was not the pattern. Try again!";
        }
        c.entered += 1;
        if c.entered == 3 {
            self.challenge = None;
            self.coins = self.coins.saturating_add(r.play_reward).min(MAX_COINS);
            self.happiness = self.happiness.saturating_add(r.care_gain).min(100);
            self.energy = self.energy.saturating_sub(r.need_decay);
            self.wins = self.wins.saturating_add(1);
            "Well remembered! Coins and happiness earned."
        } else {
            "Correct! Keep going."
        }
    }
}
