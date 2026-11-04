// AI-hint: Deterministic gameplay and real save-file controls, including locking, corruption and reset confirmation.
// AI-related: game.rs, save.rs, ui.rs
use crate::{
    config::Config,
    game::{Care, Direction, Game, Stage, MAX_COINS},
    save::{load, Store},
    ui::{Effect, Ui},
};
use crossterm::event::{KeyCode, KeyEvent, KeyEventKind, KeyModifiers};

fn rules() -> crate::game::Rules {
    Config::sample().unwrap().game
}
fn born(r: &crate::game::Rules) -> Game {
    let mut game = Game::new(r, 7);
    for _ in 0..r.hatch_ticks {
        game.tick(r);
    }
    game
}
fn key(code: KeyCode) -> KeyEvent {
    KeyEvent::new(code, KeyModifiers::NONE)
}

#[test]
fn growth_care_pause_and_death_have_deterministic_effects() {
    let r = rules();
    let mut game = Game::new(&r, 7);
    assert_eq!(game.stage(&r), Stage::Egg);
    game.care(Care::Feed, &r);
    assert_eq!(game.coins, r.starting_coins);
    for _ in 0..r.hatch_ticks {
        game.tick(&r);
    }
    assert_eq!(game.stage(&r), Stage::Baby);
    let coins = game.coins;
    game.fullness = 20;
    game.care(Care::Feed, &r);
    assert_eq!(game.fullness, 20 + r.care_gain);
    assert_eq!(game.coins, coins - r.feed_cost);
    game.ill = true;
    game.hygiene = 0;
    game.health = 30;
    game.care(Care::Clean, &r);
    assert_eq!(game.hygiene, 100);
    assert!(game.ill);
    game.care(Care::Medicine, &r);
    assert!(!game.ill);
    assert_eq!(game.health, 30 + r.care_gain);
    game.energy = 0;
    game.care(Care::Rest, &r);
    game.tick(&r);
    assert_eq!(game.energy, r.care_gain);
    game.paused = true;
    let frozen = game.clone();
    for _ in 0..100 {
        game.tick(&r);
    }
    assert_eq!(game, frozen);
    game.care(Care::Feed, &r);
    assert_eq!(game, frozen);
    game.paused = false;
    game.age_ticks = r.child_ticks;
    assert_eq!(game.stage(&r), Stage::Child);
    game.age_ticks = r.adult_ticks;
    game.happiness = 100;
    game.health = 100;
    assert!(game.thriving(&r));
    game.health = 1;
    game.ill = true;
    game.tick(&r);
    assert_eq!(game.stage(&r), Stage::Dead);
    let dead = game.clone();
    game.tick(&r);
    game.care(Care::Medicine, &r);
    assert_eq!(game, dead);
}

#[test]
fn memory_requires_reveal_then_exact_sequence_and_bounds_rewards() {
    let r = rules();
    let mut game = born(&r);
    game.happiness = 20;
    game.care(Care::Play, &r);
    let pattern = game.challenge.as_ref().unwrap().pattern;
    let watching = game.clone();
    game.guess(pattern[0], &r);
    assert_eq!(game, watching);
    for _ in 0..r.memory_reveal_ticks {
        game.tick(&r);
    }
    let coins = game.coins;
    for d in pattern {
        game.guess(d, &r);
    }
    assert_eq!(game.wins, 1);
    assert_eq!(game.coins, coins + r.play_reward);
    assert!(game.challenge.is_none());
    game.care(Care::Play, &r);
    let first = game.challenge.as_ref().unwrap().pattern[0];
    for _ in 0..r.memory_reveal_ticks {
        game.tick(&r);
    }
    game.guess(
        if first == Direction::Up {
            Direction::Down
        } else {
            Direction::Up
        },
        &r,
    );
    assert!(game.challenge.is_none());
    assert_eq!(game.wins, 1);
    game.coins = MAX_COINS;
    game.care(Care::Play, &r);
    let pattern = game.challenge.as_ref().unwrap().pattern;
    for _ in 0..r.memory_reveal_ticks {
        game.tick(&r);
    }
    for d in pattern {
        game.guess(d, &r);
    }
    assert_eq!(game.coins, MAX_COINS);
}

#[test]
fn invalid_rules_and_state_are_rejected() {
    let mut r = rules();
    r.child_ticks = r.hatch_ticks;
    assert!(r.validate().is_err());
    let mut r = rules();
    r.need_decay = 0;
    assert!(r.validate().is_err());
    let mut c = Config::sample().unwrap();
    c.state_directory = "../other".into();
    assert!(c.validate().is_err());
    let mut c = Config::sample().unwrap();
    c.autosave_seconds = 0;
    assert!(c.validate().is_err());
    let mut game = born(&rules());
    game.fullness = 101;
    assert!(game.validate().is_err());
    let mut game = born(&rules());
    game.coins = MAX_COINS + 1;
    assert!(game.validate().is_err());
}

#[test]
fn save_round_trip_freezes_offline_time_and_lock_releases_on_drop() {
    let r = rules();
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("pet.json");
    let store = Store::open(&path).unwrap();
    assert!(store.load().unwrap().is_none());
    assert!(Store::open(&path).is_err());
    let mut game = born(&r);
    game.paused = true;
    store.save(&game).unwrap();
    assert_eq!(store.load().unwrap().unwrap(), game);
    assert_eq!(load(&path).unwrap().unwrap(), game); // reading a running writer's atomic snapshot is safe
    drop(store);
    let reopened = Store::open(&path).unwrap();
    assert_eq!(reopened.load().unwrap().unwrap(), game);
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(
            std::fs::metadata(&path).unwrap().permissions().mode() & 0o777,
            0o600
        );
    }
}

#[test]
fn invalid_writes_preserve_previous_save_and_bad_reads_never_reset_it() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("pet.json");
    let store = Store::open(&path).unwrap();
    let mut game = born(&rules());
    store.save(&game).unwrap();
    let original = std::fs::read(&path).unwrap();
    game.energy = 255;
    assert!(store.save(&game).is_err());
    assert_eq!(std::fs::read(&path).unwrap(), original);
    for bytes in [
        b"{bad json".to_vec(),
        br#"{"schema":999,"game":{}}"#.to_vec(),
        vec![b'x'; 65537],
    ] {
        std::fs::write(&path, &bytes).unwrap();
        assert!(store.load().is_err());
        assert_eq!(std::fs::read(&path).unwrap(), bytes);
    }
    let mut envelope: serde_json::Value = serde_json::from_slice(&original).unwrap();
    envelope["schema"] = 999.into();
    std::fs::write(&path, serde_json::to_vec(&envelope).unwrap()).unwrap();
    assert!(store
        .load()
        .unwrap_err()
        .contains("unsupported save schema"));
}

#[test]
fn controller_menu_reset_confirmation_and_memory_repeat_filter_are_playable() {
    let r = rules();
    let mut ui = Ui::new(born(&r));
    ui.game.fullness = 10;
    ui.handle(key(KeyCode::Enter), &r);
    assert_eq!(ui.game.fullness, 10 + r.care_gain);
    ui.handle(key(KeyCode::Down), &r);
    assert_eq!(ui.selected, 1);
    ui.handle(key(KeyCode::Enter), &r);
    assert!(ui.game.challenge.is_some());
    for _ in 0..r.memory_reveal_ticks {
        ui.game.tick(&r);
    }
    let pattern = ui.game.challenge.as_ref().unwrap().pattern;
    let mut repeated = key(KeyCode::Up);
    repeated.kind = KeyEventKind::Repeat;
    let before = ui.game.clone();
    ui.handle(repeated, &r);
    assert_eq!(ui.game, before);
    for d in pattern {
        ui.handle(
            key(match d {
                Direction::Up => KeyCode::Up,
                Direction::Down => KeyCode::Down,
                Direction::Left => KeyCode::Left,
                Direction::Right => KeyCode::Right,
            }),
            &r,
        );
    }
    assert_eq!(ui.game.wins, 1);
    ui.handle(key(KeyCode::Char('n')), &r);
    let before = ui.game.clone();
    assert!(ui.confirm_reset);
    ui.handle(key(KeyCode::Esc), &r);
    assert_eq!(ui.game, before);
    ui.handle(key(KeyCode::Char('n')), &r);
    ui.handle(key(KeyCode::Enter), &r);
    assert_eq!(ui.game.stage(&r), Stage::Egg);
    assert_eq!(ui.game.wins, 0);
    assert_eq!(ui.handle(key(KeyCode::Char('q')), &r), Effect::Quit);
}

#[test]
fn insufficient_funds_and_energy_do_not_spend_or_start_games() {
    let r = rules();
    let mut game = born(&r);
    game.coins = 0;
    game.fullness = 10;
    game.health = 10;
    game.ill = true;
    let before = game.clone();
    game.care(Care::Feed, &r);
    assert_eq!(game, before);
    game.care(Care::Medicine, &r);
    assert_eq!(game, before);
    game.energy = 0;
    let before = game.clone();
    game.care(Care::Play, &r);
    assert_eq!(game, before);
    game.hygiene = 10;
    game.care(Care::Clean, &r);
    assert_eq!(game.hygiene, 100);
    assert_eq!(game.coins, 0);
}

#[test]
fn schema_one_migration_keeps_state_and_schema_two_retains_identity() {
    let r = rules();
    let mut game = Game::new(&r, 515);
    for _ in 0..r.hatch_ticks {
        game.tick(&r);
    }
    game.paused = true;
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("pet.json");
    let mut legacy_game = serde_json::to_value(&game).unwrap();
    legacy_game.as_object_mut().unwrap().remove("character");
    let original = serde_json::to_vec(&serde_json::json!({"schema":1,"game":legacy_game})).unwrap();
    std::fs::write(&path, &original).unwrap();
    let migrated = crate::save::load(&path).unwrap().unwrap();
    assert_eq!(migrated.character, 0);
    assert_eq!(migrated.age_ticks, game.age_ticks);
    assert_eq!(std::fs::read(&path).unwrap(), original);
    let store = crate::save::Store::open(&path).unwrap();
    store.save(&migrated).unwrap();
    let envelope: serde_json::Value =
        serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
    assert_eq!(envelope["schema"], 3);
    assert_eq!(store.load().unwrap().unwrap(), migrated);
    for seed in 0..512 {
        let pet = Game::new(&r, seed);
        assert!(pet.character < 16);
        assert_eq!(pet, Game::new(&r, seed));
    }
}
