// AI-hint: Versioned transmissible birth codes and pure seeded Mon identities; progress is separate.
// AI-related: game.rs, progression.rs, config.rs, ui.rs, usr/share/mios/mios.toml [mons.genetics]
use crate::{
    config::Config,
    game::Game,
    progression::{Progress, Stat, TrainingRules},
};
use serde::Deserialize;
use std::{fmt, str::FromStr};

const ALPHABET: &[u8; 32] = b"0123456789ABCDEFGHJKMNPQRSTVWXYZ";

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct MonCode(u64);
impl MonCode {
    pub fn new(seed: u64) -> Result<Self, String> {
        if seed == 0 {
            Err("Mon seed must be nonzero".into())
        } else {
            Ok(Self(seed))
        }
    }
    pub fn seed(self) -> u64 {
        self.0
    }
    pub fn compact(self) -> String {
        let mut value = (u128::from(self.0) << 10) | u128::from(checksum(self.0));
        let mut digits = [b'0'; 15];
        for d in digits.iter_mut().rev() {
            *d = ALPHABET[(value & 31) as usize];
            value >>= 5;
        }
        format!(
            "M1{}",
            String::from_utf8(digits.to_vec()).expect("ASCII alphabet")
        )
    }
}
fn checksum(seed: u64) -> u16 {
    let mut crc = 0u16;
    for byte in std::iter::once(1u8).chain(seed.to_be_bytes()) {
        crc ^= u16::from(byte) << 2;
        for _ in 0..8 {
            let top = crc & 0x200;
            crc = (crc << 1) & 0x3ff;
            if top != 0 {
                crc ^= 0x233;
            }
        }
    }
    crc
}
impl fmt::Display for MonCode {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let s = self.compact();
        write!(
            f,
            "M1-{}-{}-{}-{}",
            &s[2..6],
            &s[6..10],
            &s[10..14],
            &s[14..17]
        )
    }
}
impl FromStr for MonCode {
    type Err = String;
    fn from_str(input: &str) -> Result<Self, String> {
        let input = input.trim();
        if !input.is_empty() && input.bytes().all(|c| c.is_ascii_digit()) {
            return input
                .parse::<u64>()
                .map_err(|_| "Mon seed exceeds 64 bits".to_string())
                .and_then(Self::new);
        }
        let text = input.to_ascii_uppercase();
        let digits = if text.len() == 17 && text.starts_with("M1") {
            text[2..].to_string()
        } else if text.len() == 21
            && text.starts_with("M1-")
            && [7, 12, 17].iter().all(|&i| text.as_bytes()[i] == b'-')
        {
            text[3..].replace('-', "")
        } else {
            return Err("Expected decimal seed or M1-XXXX-XXXX-XXXX-XXX birth code".into());
        };
        if digits.len() != 15 {
            return Err("Mon code must contain fifteen payload characters".into());
        }
        let mut value = 0u128;
        for mut c in digits.bytes() {
            c = match c {
                b'O' => b'0',
                b'I' | b'L' => b'1',
                _ => c,
            };
            let n = ALPHABET
                .iter()
                .position(|&a| a == c)
                .ok_or("Invalid Mon code character")?;
            value = (value << 5) | n as u128;
        }
        if value >> 74 != 0 {
            return Err("Mon code exceeds 64-bit seed".into());
        }
        let seed = (value >> 10) as u64;
        if value & 1023 != u128::from(checksum(seed)) {
            return Err("Mon code checksum mismatch".into());
        }
        Self::new(seed)
    }
}
pub fn mix(mut n: u64) -> u64 {
    n = (n ^ (n >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
    n = (n ^ (n >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
    n ^ (n >> 31)
}
pub fn bonus_index(origin_seed: u64) -> usize {
    (mix(origin_seed ^ 0x626f_6e75_732d_7631) % 5) as usize
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GeneticsRules {
    pub likes: Vec<String>,
    pub dislikes: Vec<String>,
    pub traits: Vec<String>,
    pub tropes: Vec<String>,
}
impl GeneticsRules {
    pub fn validate(&self) -> Result<(), String> {
        for (pool, min) in [
            (&self.likes, 2),
            (&self.dislikes, 1),
            (&self.traits, 1),
            (&self.tropes, 1),
        ] {
            let unique = pool.iter().collect::<std::collections::BTreeSet<_>>();
            if !(min..=32).contains(&pool.len())
                || unique.len() != pool.len()
                || pool.iter().any(|s| {
                    s.is_empty()
                        || s.len() > 32
                        || s.trim() != s
                        || !s.bytes().all(|b| (b' '..=b'~').contains(&b))
                })
            {
                return Err("mons.genetics: require distinct printable ASCII choices (1..32 bytes), two likes and 1..32 entries per pool".into());
            }
        }
        Ok(())
    }
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Profile {
    pub code: String,
    pub name: String,
    pub species: String,
    pub archetype: String,
    pub trait_label: String,
    pub trope: String,
    pub likes: Vec<String>,
    pub dislikes: Vec<String>,
    pub bonus_stat: String,
    pub body: u8,
    pub accent: u8,
    pub hatch_stats: [Stat; 5],
}
impl Profile {
    pub fn for_game(game: &Game, config: &Config) -> Option<Self> {
        let seed = game.genesis_seed?;
        if game.progress.legacy || Progress::new(seed).origin_seed != game.progress.origin_seed {
            return None;
        }
        if game.character != ((seed % 8) + ((seed >> 8) % 2) * 8) as u8 {
            return None;
        }
        let code = MonCode::new(seed).ok()?.compact();
        let pick = |domain: u64, n: usize| (mix(seed ^ domain) % n as u64) as usize;
        let c = config.characters.iter().find(|c| c.id == game.character)?;
        let archetypes = config
            .game
            .training
            .personality_colors
            .keys()
            .collect::<Vec<_>>();
        let archetype = archetypes[pick(0x6172_6368_6574_7970, archetypes.len())].clone();
        let rules = &config.genetics;
        let first = pick(0x6c69_6b65_732d_6131, rules.likes.len());
        let second =
            (first + 1 + pick(0x6c69_6b65_732d_6231, rules.likes.len() - 1)) % rules.likes.len();
        let birth = Progress::birth_preview(seed, &config.game.training);
        let (body, accent) = config.game.training.palette(c.shape, &archetype).ok()?;
        Some(Self {
            code,
            name: birth.name,
            species: c.species.clone(),
            archetype,
            trait_label: rules.traits[pick(0x7472_6169_742d_7631, rules.traits.len())].clone(),
            trope: rules.tropes[pick(0x7472_6f70_652d_7631, rules.tropes.len())].clone(),
            likes: vec![rules.likes[first].clone(), rules.likes[second].clone()],
            dislikes: vec![
                rules.dislikes[pick(0x6469_736c_696b_6573, rules.dislikes.len())].clone(),
            ],
            bonus_stat: format!(
                "{} (+{} hatch)",
                TrainingRules::label(bonus_index(birth.origin_seed)),
                config.game.training.hatch_bonus
            ),
            body,
            accent,
            hatch_stats: birth.stats,
        })
    }
    /// Build the current individual's appearance and voice from configured species/personality.
    pub fn character(
        &self,
        base: &crate::characters::Character,
        config: &Config,
    ) -> crate::characters::Character {
        let mut c = config
            .characters
            .iter()
            .find(|c| c.archetype == self.archetype)
            .unwrap_or(base)
            .clone();
        c.id = base.id;
        c.shape = base.shape;
        c.species = base.species.clone();
        c.name = self.name.clone();
        c.body = self.body;
        c.accent = self.accent;
        c.hobby = self.likes.join(" and ");
        c
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn codes_round_trip_entire_seed_and_reject_single_symbol_damage() {
        for seed in [1, 2, 3, 256, 257, u64::MAX, u64::MAX - 1, 0x123456789abcdef] {
            let code = MonCode::new(seed).unwrap();
            assert_eq!(code.to_string().parse::<MonCode>().unwrap(), code);
            assert_eq!(
                code.compact()
                    .to_ascii_lowercase()
                    .parse::<MonCode>()
                    .unwrap(),
                code
            );
            for i in 2..17 {
                let mut damaged = code.compact().into_bytes();
                let old = damaged[i];
                for &replacement in ALPHABET {
                    if replacement == old {
                        continue;
                    }
                    damaged[i] = replacement;
                    assert!(String::from_utf8(damaged.clone())
                        .unwrap()
                        .parse::<MonCode>()
                        .is_err());
                }
            }
        }
    }
    #[test]
    fn malformed_zero_overflow_and_version_are_rejected() {
        for text in [
            "0",
            "18446744073709551616",
            "M2-0000-0000-0000-000",
            "M1000000000000000",
            "M1ZZZZZZZZZZZZZZZ",
            "M1-000000000000000",
            "M1\u{1b}00000000000000",
        ] {
            assert!(text.parse::<MonCode>().is_err(), "{text:?}");
        }
        assert_eq!("123".parse::<MonCode>().unwrap().seed(), 123);
        let mut extra_hyphen = MonCode::new(1).unwrap().to_string().into_bytes();
        extra_hyphen[3] = b'-';
        assert!(String::from_utf8(extra_hyphen)
            .unwrap()
            .parse::<MonCode>()
            .is_err());
    }
    #[test]
    fn birth_identity_is_pure_diverse_and_independent_of_game_clock() {
        let config = Config::sample().unwrap();
        let mut combinations = std::collections::BTreeSet::new();
        for seed in 1..=512 {
            let mut game = Game::new(&config.game, seed);
            let before = game.clone();
            let profile = Profile::for_game(&game, &config).unwrap();
            assert_eq!(game, before);
            assert_eq!(profile.code.len(), 17);
            assert_ne!(profile.likes[0], profile.likes[1]);
            assert!(profile
                .hatch_stats
                .iter()
                .all(|s| s.value > 0 && s.value <= 100 && s.aptitude <= 4));
            combinations.insert((game.character, profile.archetype.clone()));
            for _ in 0..config.game.hatch_ticks {
                game.tick(&config.game);
            }
            game.prepare_progress(&config.game).unwrap();
            assert_eq!(game.progress.name, profile.name);
            assert_eq!(game.progress.stats, profile.hatch_stats);
            assert_eq!(Profile::for_game(&game, &config).unwrap(), profile);
        }
        assert!(combinations.len() > 100);
        let mut old = Game::new(&config.game, 1);
        old.genesis_seed = Some(2);
        assert!(old.validate().is_err());
        assert!(Profile::for_game(&old, &config).is_none());
        old.genesis_seed = None;
        assert!(Profile::for_game(&old, &config).is_none());
    }
    #[test]
    fn unsafe_or_degenerate_identity_pools_fail_configuration() {
        let mut config = Config::sample().unwrap();
        config.genetics.likes = vec!["same".into(), "same".into()];
        assert!(config.validate().is_err());
        config.genetics.likes = vec!["valid".into(), "\x1b[2J".into()];
        assert!(config.validate().is_err());
    }
}
