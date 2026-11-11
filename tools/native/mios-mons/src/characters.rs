// AI-hint: Validated original animal catalogue and pure state-aware speech; presentation never advances gameplay RNG.
// AI-related: game.rs, config.rs, usr/share/mios/mios.toml [mons.characters]
// AI-functions: validate_catalogue, character, speech, thought
use std::collections::HashSet;

use serde::Deserialize;

use crate::game::{Game, Rules, Stage};

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Character {
    pub id: u8,
    pub name: String,
    pub species: String,
    pub archetype: String,
    pub hobby: String,
    pub shape: u8,
    pub body: u8,
    pub accent: u8,
    pub dialogue: Vec<String>,
    pub thoughts: Vec<String>,
    pub hungry: String,
    pub sleepy: String,
    pub ill: String,
    pub hatch: String,
    pub adult: String,
    pub dead: String,
}

const ARCHETYPES: [&str; 8] = [
    "cozy",
    "athletic",
    "grumbly",
    "showy",
    "neighborly",
    "sunny",
    "refined",
    "protective",
];

/// Bounds are terminal-safety limits; all display copy comes from the SSOT.
/// Reject controls (including ESC), Unicode and blank or padded text before raw mode.
fn text_valid(text: &str, max: usize) -> bool {
    !text.is_empty()
        && text.len() <= max
        && text.trim() == text
        && text.bytes().all(|b| (b' '..=b'~').contains(&b))
}

pub fn validate_catalogue(catalogue: &[Character]) -> Result<(), String> {
    if !(16..=64).contains(&catalogue.len()) {
        return Err("mons.characters: catalogue must contain 16..=64 characters".into());
    }
    let mut ids = HashSet::new();
    let mut names = HashSet::new();
    let mut archetypes = HashSet::new();
    for c in catalogue {
        let fail = |reason: &str| format!("mons.characters[{}]: {reason}", c.id);
        if !ids.insert(c.id) || usize::from(c.id) >= catalogue.len() {
            return Err(fail("IDs must be unique and contiguous from zero"));
        }
        if !text_valid(&c.name, 48) || !names.insert(c.name.to_ascii_lowercase()) {
            return Err(fail("names must be unique printable ASCII of 1..=48 bytes"));
        }
        if !text_valid(&c.species, 48) || !text_valid(&c.hobby, 80) {
            return Err(fail(
                "species/hobby must be printable ASCII of 1..=48/80 bytes",
            ));
        }
        if !ARCHETYPES.contains(&c.archetype.as_str()) {
            return Err(fail("unrecognized personality archetype"));
        }
        archetypes.insert(c.archetype.as_str());
        if c.shape > 15 || c.body > 15 || c.accent > 15 {
            return Err(fail("shape and body/accent palette indices must be 0..=15"));
        }
        for pool in [&c.dialogue, &c.thoughts] {
            if !(2..=16).contains(&pool.len()) || pool.iter().any(|s| !text_valid(s, 160)) {
                return Err(fail(
                    "dialogue/thought pools require 2..=16 printable ASCII lines of 1..=160 bytes",
                ));
            }
        }
        if [&c.hungry, &c.sleepy, &c.ill, &c.hatch, &c.adult, &c.dead]
            .iter()
            .any(|s| !text_valid(s, 160))
        {
            return Err(fail(
                "state voices must be printable ASCII of 1..=160 bytes",
            ));
        }
    }
    if archetypes.len() != ARCHETYPES.len() {
        return Err("mons.characters: all eight personality archetypes must be represented".into());
    }
    Ok(())
}

/// Requires a validated, nonempty catalogue. Caller validates the saved active ID.
/// Search by identity rather than assuming TOML rows are in ID order.
pub fn character(catalogue: &[Character], id: u8) -> &Character {
    catalogue
        .iter()
        .find(|c| c.id == id)
        .unwrap_or(&catalogue[0])
}

/// Care needs outrank growth celebrations; growth outranks everyday flavour.
/// Both speech and thoughts use the character's own state-specific voice.
fn state_voice<'a>(c: &'a Character, game: &Game, rules: &Rules) -> Option<&'a str> {
    match game.stage(rules) {
        Stage::Egg => return Some(&c.hatch),
        Stage::Dead => return Some(&c.dead),
        _ => {}
    }
    if game.ill || game.health <= rules.illness_threshold {
        Some(&c.ill)
    } else if game.fullness <= rules.illness_threshold {
        Some(&c.hungry)
    } else if game.sleeping || game.energy <= rules.illness_threshold {
        Some(&c.sleepy)
    } else if game.age_ticks == rules.hatch_ticks || game.age_ticks == rules.child_ticks {
        Some(&c.hatch)
    } else if game.age_ticks == rules.adult_ticks {
        Some(&c.adult)
    } else {
        None
    }
}

/// Variant is supplied by the UI's cosmetic clock; no game mutation or RNG calls.
pub fn speech(character: &Character, game: &Game, rules: &Rules, variant: u64) -> String {
    state_voice(character, game, rules)
        .unwrap_or_else(|| {
            &character.dialogue[(variant % character.dialogue.len() as u64) as usize]
        })
        .to_owned()
}

pub fn thought(character: &Character, game: &Game, rules: &Rules, variant: u64) -> String {
    state_voice(character, game, rules)
        .unwrap_or_else(|| {
            &character.thoughts[(variant % character.thoughts.len() as u64) as usize]
        })
        .to_owned()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn catalogue() -> Vec<Character> {
        (0..16)
            .map(|id| Character {
                id,
                name: format!("Animal{id}"),
                species: "cat / neko".into(),
                archetype: ARCHETYPES[usize::from(id) % 8].into(),
                hobby: format!("collecting pebble number {id}"),
                shape: id,
                body: id,
                accent: (id + 5) % 16,
                dialogue: vec!["A sunny stone!".into(), "A round stone!".into()],
                thoughts: vec!["One pebble.".into(), "Two pebbles.".into()],
                hungry: "A snack, please.".into(),
                sleepy: "Time for a nest.".into(),
                ill: "A little care, please.".into(),
                hatch: "A tiny new adventure.".into(),
                adult: "I have grown into my paws.".into(),
                dead: "Remember our warm afternoons.".into(),
            })
            .collect()
    }

    fn rules() -> Rules {
        Rules {
            tick_seconds: 1,
            hatch_ticks: 2,
            child_ticks: 5,
            adult_ticks: 10,
            need_decay: 1,
            initial_need: 80,
            illness_threshold: 20,
            healthy_threshold: 60,
            healthy_regen: 1,
            health_loss: 1,
            care_gain: 20,
            play_reward: 5,
            feed_cost: 1,
            medicine_cost: 2,
            starting_coins: 10,
            memory_reveal_ticks: 2,
            training: crate::config::Config::sample().unwrap().game.training,
        }
    }

    fn healthy(rules: &Rules) -> Game {
        let mut game = Game::new(rules, 3716);
        game.age_ticks = rules.adult_ticks + 1;
        game
    }

    #[test]
    fn complete_catalogue_and_unordered_lookup() {
        let mut all = catalogue();
        validate_catalogue(&all).unwrap();
        all.reverse();
        validate_catalogue(&all).unwrap();
        assert_eq!(character(&all, 3).id, 3);
        assert_eq!(character(&all, 255).id, all[0].id);
        let sample = all[0].clone();
        all.extend(
            (16..64)
                .map(|id| {
                    let mut c = sample.clone();
                    c.id = id;
                    c.name = format!("Extra{id}");
                    c
                })
                .collect::<Vec<_>>(),
        );
        validate_catalogue(&all).unwrap();
        all.push(all[0].clone());
        assert!(validate_catalogue(&all).is_err());
    }

    #[test]
    fn rejects_duplicates_gaps_missing_types_and_truncation() {
        let mut all = catalogue();
        all[1].id = 0;
        assert!(validate_catalogue(&all).is_err());
        all = catalogue();
        all[15].id = 16;
        assert!(validate_catalogue(&all).is_err());
        all = catalogue();
        all[1].name = all[0].name.to_ascii_uppercase();
        assert!(validate_catalogue(&all).is_err());
        all = catalogue();
        for c in &mut all {
            if c.archetype == "refined" {
                c.archetype = "cozy".into();
            }
        }
        assert!(validate_catalogue(&all).is_err());
        all = catalogue();
        all[0].archetype = "invented".into();
        assert!(validate_catalogue(&all).is_err());
        assert!(validate_catalogue(&catalogue()[..15]).is_err());
        assert!(validate_catalogue(&[]).is_err());
    }

    #[test]
    fn rejects_terminal_injection_blank_oversize_and_invalid_palette() {
        for unsafe_text in [
            "",
            " ",
            " padded",
            "padded ",
            "\x1b[31m",
            "line\nbreak",
            "tab\t",
            "bad\x7f",
            "neko\u{732b}",
        ] {
            let mut all = catalogue();
            all[0].thoughts[0] = unsafe_text.into();
            assert!(validate_catalogue(&all).is_err(), "{unsafe_text:?}");
        }
        let mut all = catalogue();
        all[0].dead = "x".repeat(161);
        assert!(validate_catalogue(&all).is_err());
        all = catalogue();
        all[0].dialogue.truncate(1);
        assert!(validate_catalogue(&all).is_err());
        all = catalogue();
        all[0].thoughts = vec!["Too many.".into(); 17];
        assert!(validate_catalogue(&all).is_err());
        for field in 0..3 {
            let mut all = catalogue();
            match field {
                0 => all[0].shape = 16,
                1 => all[0].body = 16,
                _ => all[0].accent = 16,
            }
            assert!(validate_catalogue(&all).is_err());
        }
    }

    #[test]
    fn schema_rejects_unknown_and_missing_fields() {
        let mut value = serde_json::json!({
            "id":0, "name":"Mio", "species":"cat / neko", "archetype":"cozy",
            "hobby":"quilting", "shape":0, "body":0, "accent":5,
            "dialogue":["One.","Two."], "thoughts":["Three.","Four."],
            "hungry":"Food.", "sleepy":"Rest.", "ill":"Care.",
            "hatch":"Hello.", "adult":"Grown.", "dead":"Goodbye."
        });
        assert!(serde_json::from_value::<Character>(value.clone()).is_ok());
        value["unexpected"] = true.into();
        assert!(serde_json::from_value::<Character>(value.clone()).is_err());
        value.as_object_mut().unwrap().remove("unexpected");
        value.as_object_mut().unwrap().remove("dead");
        assert!(serde_json::from_value::<Character>(value).is_err());
    }

    #[test]
    fn state_priorities_and_growth_precede_flavour() {
        let c = &catalogue()[0];
        let r = rules();
        let assert_voice = |g: &Game, expected: &str| {
            assert_eq!(speech(c, g, &r, 1), expected);
            assert_eq!(thought(c, g, &r, u64::MAX), expected);
        };
        let mut g = healthy(&r);
        g.age_ticks = 0;
        g.ill = true;
        g.fullness = 0;
        g.energy = 0;
        assert_voice(&g, &c.hatch);
        g.health = 0;
        assert_voice(&g, &c.dead);
        g = healthy(&r);
        g.ill = true;
        g.fullness = 0;
        g.sleeping = true;
        assert_voice(&g, &c.ill);
        g.ill = false;
        g.health = r.illness_threshold;
        assert_voice(&g, &c.ill);
        g.health = 100;
        assert_voice(&g, &c.hungry);
        g.fullness = 100;
        assert_voice(&g, &c.sleepy);
        g.sleeping = false;
        g.energy = r.illness_threshold;
        assert_voice(&g, &c.sleepy);
        g.energy = 100;
        for age in [r.hatch_ticks, r.child_ticks] {
            g.age_ticks = age;
            assert_voice(&g, &c.hatch);
        }
        g.age_ticks = r.adult_ticks;
        assert_voice(&g, &c.adult);
        g.fullness = 0;
        assert_voice(&g, &c.hungry);
    }

    #[test]
    fn healthy_variants_are_deterministic_and_leave_game_rng_unchanged() {
        let all = catalogue();
        let r = rules();
        let mut g = healthy(&r);
        g.wins = 42;
        let before = g.clone();
        for c in &all {
            for variant in [0, 1, 2, 97, u64::MAX] {
                assert_eq!(
                    speech(c, &g, &r, variant),
                    c.dialogue[(variant % 2) as usize]
                );
                assert_eq!(
                    thought(c, &g, &r, variant),
                    c.thoughts[(variant % 2) as usize]
                );
                assert_eq!(speech(c, &g, &r, variant), speech(c, &g, &r, variant));
            }
        }
        assert_eq!(g, before);
        assert_eq!(g.visual_seed(), before.visual_seed());
    }
}
