// AI-hint: Versioned, bounded per-user saves with an OS-held single-writer lock and atomic durable replacement.
// AI-related: game.rs, main.rs, usr/share/doc/mios/tamagotchi-tui.md
use crate::game::Game;
use serde::{Deserialize, Serialize};
use std::{
    env,
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
};

const SCHEMA: u32 = 4;
const MAX_SAVE_BYTES: u64 = 65_536;

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Envelope<T> {
    schema: u32,
    game: T,
}

pub fn default_path(directory: &str) -> Result<PathBuf, String> {
    let base = env::var_os("XDG_STATE_HOME")
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
        .or_else(|| {
            env::var_os("HOME")
                .or_else(|| env::var_os("USERPROFILE"))
                .filter(|v| !v.is_empty())
                .map(|v| PathBuf::from(v).join(".local/state"))
        })
        .ok_or("No state home: use --save PATH, --ephemeral or --sample")?;
    if !base.is_absolute() {
        return Err("State home must be absolute; use --save PATH or --sample".into());
    }
    Ok(base.join(directory).join("pet.json"))
}

pub fn load(path: &Path) -> Result<Option<Game>, String> {
    load_with_rules(path, crate::habitat::vendor_rules()?)
}

pub fn load_with_rules(
    path: &Path,
    rules: &crate::habitat::HabitatRules,
) -> Result<Option<Game>, String> {
    rules.validate()?;
    let file = match File::open(path) {
        Ok(f) => f,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(format!("read {}: {e}", path.display())),
    };
    let mut bytes = Vec::new();
    file.take(MAX_SAVE_BYTES + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if bytes.len() as u64 > MAX_SAVE_BYTES {
        return Err("save exceeds 64 KiB limit".into());
    }
    let envelope: Envelope<serde_json::Value> = serde_json::from_slice(&bytes)
        .map_err(|e| format!("invalid save {}: {e}", path.display()))?;
    if !matches!(envelope.schema, 1..=SCHEMA) {
        return Err(format!("unsupported save schema {}", envelope.schema));
    }
    let has_habitat = envelope.game.get("habitat").is_some();
    if !has_habitat && envelope.schema >= 3 {
        return Err(format!(
            "schema-{} save is missing habitat",
            envelope.schema
        ));
    }
    let has_progress = envelope.game.get("progress").is_some();
    if !has_progress && envelope.schema == SCHEMA {
        return Err("schema-4 save is missing progress".into());
    }
    // Deserialize the original bytes again into strict structs. Going through
    // Value here would silently collapse duplicate game/habitat field names.
    let mut game: Game = serde_json::from_slice::<Envelope<Game>>(&bytes)
        .map_err(|e| format!("invalid save {}: {e}", path.display()))?
        .game;
    if !has_habitat {
        game.habitat = crate::habitat::Habitat::new(game.visual_seed());
    }
    if envelope.schema < SCHEMA {
        // Refuse malformed supplied progression even in an older envelope.
        // Actual legacy saves have no progression; seed only from saved state.
        if has_progress {
            game.progress.validate()?;
        }
        game.progress = crate::progression::Progress::legacy(game.visual_seed());
    }
    game.validate_habitat(rules)?;
    // Migration is in memory only. Offline time, including clock reversal, is frozen.
    Ok(Some(game))
}

pub struct Store {
    path: PathBuf,
    _lock: File,
}
impl Store {
    pub fn open(path: &Path) -> Result<Self, String> {
        let path = std::path::absolute(path).map_err(|e| e.to_string())?;
        let parent = path.parent().ok_or("save path needs a parent directory")?;
        let mut builder = fs::DirBuilder::new();
        builder.recursive(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::DirBuilderExt;
            builder.mode(0o700);
        }
        builder
            .create(parent)
            .map_err(|e| format!("create state directory: {e}"))?;
        let mut lock_name = path.as_os_str().to_owned();
        lock_name.push(".lock");
        let mut options = OpenOptions::new();
        options.read(true).write(true).create(true).truncate(false);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let lock = options
            .open(PathBuf::from(lock_name))
            .map_err(|e| format!("open save lock: {e}"))?;
        lock.try_lock()
            .map_err(|e| format!("save is already in use or cannot be locked: {e}"))?;
        Ok(Self { path, _lock: lock })
    }
    pub fn load(&self) -> Result<Option<Game>, String> {
        load(&self.path)
    }
    pub fn load_with_rules(
        &self,
        rules: &crate::habitat::HabitatRules,
    ) -> Result<Option<Game>, String> {
        load_with_rules(&self.path, rules)
    }
    pub fn save(&self, game: &Game) -> Result<(), String> {
        self.save_with_rules(game, crate::habitat::vendor_rules()?)
    }
    pub fn save_with_rules(
        &self,
        game: &Game,
        rules: &crate::habitat::HabitatRules,
    ) -> Result<(), String> {
        game.validate_habitat(rules)?;
        let parent = self
            .path
            .parent()
            .ok_or("save path needs a parent directory")?;
        let mut temp = tempfile::NamedTempFile::new_in(parent).map_err(|e| e.to_string())?;
        serde_json::to_writer(
            &mut temp,
            &Envelope {
                schema: SCHEMA,
                game: game.clone(),
            },
        )
        .map_err(|e| e.to_string())?;
        temp.write_all(b"\n").map_err(|e| e.to_string())?;
        temp.as_file().sync_all().map_err(|e| e.to_string())?;
        temp.persist(&self.path)
            .map_err(|e| format!("atomic save failed: {e}"))?;
        #[cfg(unix)]
        File::open(parent)
            .and_then(|f| f.sync_all())
            .map_err(|e| e.to_string())?;
        Ok(())
    }
}

#[cfg(test)]
mod habitat_tests {
    use super::*;
    use crate::{
        config::Config,
        game::{Care, Challenge, Direction},
    };

    #[test]
    fn legacy_schemas_migrate_only_in_memory_and_preserve_every_original_field() {
        let c = Config::sample().unwrap();
        for schema in [1, 2, 3] {
            let directory = tempfile::tempdir().unwrap();
            let path = directory.path().join("pet.json");
            let mut game = Game::new(&c.game, 515);
            game.age_ticks = 37;
            game.fullness = 31;
            game.happiness = 49;
            game.hygiene = 52;
            game.energy = 63;
            game.health = 88;
            game.coins = 42;
            game.wins = 3;
            game.paused = true;
            game.ill = true;
            game.challenge = Some(Challenge {
                pattern: [Direction::Left, Direction::Down, Direction::Right],
                entered: 1,
                reveal: 0,
            });
            let mut legacy = serde_json::to_value(&game).unwrap();
            legacy.as_object_mut().unwrap().remove("progress");
            if schema < 3 {
                legacy.as_object_mut().unwrap().remove("habitat");
            }
            if schema == 1 {
                legacy.as_object_mut().unwrap().remove("character");
            }
            let bytes =
                serde_json::to_vec(&serde_json::json!({"schema":schema,"game":legacy})).unwrap();
            fs::write(&path, &bytes).unwrap();
            let migrated = load(&path).unwrap().unwrap();
            assert_eq!(load(&path).unwrap().unwrap(), migrated);
            let mut original_fields = serde_json::to_value(&migrated).unwrap();
            original_fields.as_object_mut().unwrap().remove("progress");
            if schema < 3 {
                original_fields.as_object_mut().unwrap().remove("habitat");
            }
            if schema == 1 {
                assert_eq!(migrated.character, 0);
                original_fields.as_object_mut().unwrap().remove("character");
            }
            assert_eq!(original_fields, legacy);
            assert_eq!(fs::read(&path).unwrap(), bytes);
            assert_eq!(fs::read_dir(directory.path()).unwrap().count(), 1);
            let room = migrated.habitat;
            let store = Store::open(&path).unwrap();
            store.save(&migrated).unwrap();
            let encoded: serde_json::Value =
                serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
            assert_eq!(encoded["schema"], 4);
            assert_eq!(store.load().unwrap().unwrap(), migrated);
            let mut advanced = migrated;
            advanced.challenge = None;
            advanced.paused = false;
            advanced.care(Care::Play, &c.game);
            assert_eq!(advanced.habitat, room);
            store.save(&advanced).unwrap();
            assert_eq!(store.load().unwrap().unwrap().habitat, room);
        }
    }

    #[test]
    fn purchases_placements_and_layered_catalogues_round_trip_with_writer_lock_intact() {
        let c = Config::sample().unwrap();
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("pet.json");
        let store = Store::open(&path).unwrap();
        let mut game = Game::new(&c.game, 99);
        game.buy_item(0, &c.habitat).unwrap();
        game.toggle_item(0, &c.habitat).unwrap();
        game.buy_item(1, &c.habitat).unwrap();
        game.toggle_item(1, &c.habitat).unwrap();
        store.save(&game).unwrap();
        assert_eq!(store.load().unwrap().unwrap(), game);
        assert!(Store::open(&path).is_err());
        let previous = fs::read(&path).unwrap();
        game.habitat.placed |= 1 << 63;
        assert!(store.save(&game).is_err());
        assert_eq!(fs::read(&path).unwrap(), previous);
        let mut rules = c.habitat;
        let mut item = rules.items[0].clone();
        item.id = rules.items.len() as u8;
        let id = item.id;
        rules.items.push(item);
        game = Game::new(&c.game, 99);
        game.buy_item(id, &rules).unwrap();
        game.toggle_item(id, &rules).unwrap();
        store.save_with_rules(&game, &rules).unwrap();
        assert_eq!(store.load_with_rules(&rules).unwrap().unwrap(), game);
        assert!(store.load().is_err());
        assert!(Store::open(&path).is_err());
    }

    #[test]
    fn malformed_habitat_and_unknown_schemas_are_refused_without_writes() {
        let c = Config::sample().unwrap();
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("pet.json");
        let game = Game::new(&c.game, 7);
        let original = serde_json::json!({"schema":3,"game":game});
        for case in 0..16 {
            let mut value = original.clone();
            match case {
                0 => {
                    value["schema"] = 99.into();
                    value["game"] = serde_json::json!({});
                }
                1 => value["schema"] = 0.into(),
                2 => {
                    value["game"].as_object_mut().unwrap().remove("habitat");
                }
                3 => value["game"]["habitat"] = serde_json::Value::Null,
                4 => value["game"]["habitat"]["seed"] = 0.into(),
                5 => value["game"]["habitat"]["seed"] = (-1).into(),
                6 => value["game"]["habitat"]["seed"] = "seed".into(),
                7 => value["game"]["habitat"]["theme"] = 255.into(),
                8 => value["game"]["habitat"]["owned"] = (1u64 << 63).into(),
                9 => value["game"]["habitat"]["owned"] = (1u64 << 10).into(),
                10 => value["game"]["habitat"]["placed"] = 1.into(),
                11 => value["game"]["habitat"]["placed"] = (1u64 << 10).into(),
                12 => value["game"]["habitat"]["unknown"] = true.into(),
                13 => {
                    value["schema"] = 1.into();
                    value["game"].as_object_mut().unwrap().remove("habitat");
                    value["game"]["rng"] = (-1).into();
                }
                14 => value["game"]["habitat"]["owned"] = (-1).into(),
                _ => {
                    value["game"]["habitat"]
                        .as_object_mut()
                        .unwrap()
                        .remove("placed");
                }
            }
            let bytes = serde_json::to_vec(&value).unwrap();
            fs::write(&path, &bytes).unwrap();
            let error = load(&path).unwrap_err();
            if case < 2 {
                assert!(error.contains("unsupported save schema"));
            }
            assert_eq!(fs::read(&path).unwrap(), bytes);
            assert_eq!(fs::read_dir(directory.path()).unwrap().count(), 1);
        }
        let encoded = serde_json::to_string(&original).unwrap();
        for duplicate in [
            encoded.replace("\"seed\":", "\"seed\":0,\"seed\":"),
            encoded.replace("\"coins\":", "\"coins\":0,\"coins\":"),
        ] {
            fs::write(&path, duplicate.as_bytes()).unwrap();
            assert!(load(&path).unwrap_err().contains("duplicate field"));
            assert_eq!(fs::read_to_string(&path).unwrap(), duplicate);
        }
    }
}

#[cfg(test)]
mod progression_tests {
    use super::*;
    use crate::config::Config;

    #[test]
    fn schema_four_requires_strict_progress_and_never_rewrites_a_refused_save() {
        let c = Config::sample().unwrap();
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("pet.json");
        let mut game = Game::new(&c.game, 482);
        for _ in 0..c.game.hatch_ticks {
            game.tick(&c.game);
        }
        let original = serde_json::json!({"schema":4,"game":game});
        for case in 0..21 {
            let mut value = original.clone();
            match case {
                0 => {
                    value["game"].as_object_mut().unwrap().remove("progress");
                }
                1 => value["game"]["progress"] = serde_json::Value::Null,
                2 => value["game"]["progress"]["origin_seed"] = 0.into(),
                3 => value["game"]["progress"]["rng"] = 0.into(),
                4 => value["game"]["progress"]["stats"][0]["level"] = 100.into(),
                5 => value["game"]["progress"]["stats"][0]["xp"] = 1_000_001.into(),
                6 => value["game"]["progress"]["stats"][0]["aptitude"] = 5.into(),
                7 => value["game"]["progress"]["name"] = "Name\u{1b}".into(),
                8 => value["game"]["progress"]["total_training"] = 1_000_001.into(),
                9 => value["game"]["progress"]["last_train_tick"] = 1.into(),
                10 => value["game"]["progress"]["unknown"] = true.into(),
                11 => value["game"]["progress"]["stats"][0]["unknown"] = true.into(),
                12 => {
                    value["game"]["progress"]
                        .as_object_mut()
                        .unwrap()
                        .remove("origin_seed");
                }
                13 => value["game"]["progress"]["stats"] = serde_json::json!([]),
                14 => value["game"]["progress"]["stats"][0]["value"] = 0.into(),
                15 => value["game"]["progress"]["hatched"] = false.into(),
                16 => value["game"]["progress"]["name"] = "".into(),
                17 => {
                    value["game"]["progress"]["total_training"] = 1.into();
                    value["game"]["progress"]["last_train_tick"] = (game.age_ticks + 1).into();
                }
                18 => value["game"]["progress"]["origin_seed"] = (-1).into(),
                19 => {
                    value["game"].as_object_mut().unwrap().remove("habitat");
                }
                _ => value["game"]["progress"]["stats"][0]["value"] = 101.into(),
            }
            let bytes = serde_json::to_vec(&value).unwrap();
            fs::write(&path, &bytes).unwrap();
            assert!(load(&path).is_err(), "case {case}");
            assert_eq!(fs::read(&path).unwrap(), bytes);
            assert_eq!(fs::read_dir(directory.path()).unwrap().count(), 1);
        }
        let encoded = serde_json::to_string(&original).unwrap();
        for duplicate in [
            encoded.replace("\"progress\":", "\"progress\":{},\"progress\":"),
            encoded.replace("\"origin_seed\":", "\"origin_seed\":0,\"origin_seed\":"),
            encoded.replace("\"level\":", "\"level\":0,\"level\":"),
            encoded.replace(
                "\"total_training\":",
                "\"total_training\":0,\"total_training\":",
            ),
        ] {
            fs::write(&path, duplicate.as_bytes()).unwrap();
            assert!(load(&path).is_err());
            assert_eq!(fs::read_to_string(&path).unwrap(), duplicate);
        }
    }

    #[test]
    fn legacy_snapshot_preparation_is_read_only_offline_frozen_and_keeps_identity() {
        let c = Config::sample().unwrap();
        for schema in 1..=3 {
            let directory = tempfile::tempdir().unwrap();
            let path = directory.path().join("pet.json");
            let mut original = Game::new(&c.game, 71);
            original.age_ticks = c.game.adult_ticks + 5;
            original.paused = true;
            original.character = 11;
            original.wins = 7;
            original.coins = 61;
            let mut legacy = serde_json::to_value(&original).unwrap();
            legacy.as_object_mut().unwrap().remove("progress");
            if schema < 3 {
                legacy.as_object_mut().unwrap().remove("habitat");
            }
            if schema == 1 {
                legacy.as_object_mut().unwrap().remove("character");
            }
            let bytes =
                serde_json::to_vec(&serde_json::json!({"schema":schema,"game":legacy})).unwrap();
            fs::write(&path, &bytes).unwrap();
            let mut a = load(&path).unwrap().unwrap();
            let mut b = load(&path).unwrap().unwrap();
            a.prepare_progress(&c.game).unwrap();
            b.prepare_progress(&c.game).unwrap();
            assert_eq!(a, b);
            assert!(a.progress.hatched && a.progress.legacy && a.progress.name.is_empty());
            assert_eq!(a.visual_seed(), original.visual_seed());
            let mut after = serde_json::to_value(&a).unwrap();
            after.as_object_mut().unwrap().remove("progress");
            if schema < 3 {
                after.as_object_mut().unwrap().remove("habitat");
            }
            if schema == 1 {
                after.as_object_mut().unwrap().remove("character");
            }
            assert_eq!(after, legacy);
            assert_eq!(fs::read(&path).unwrap(), bytes);
            assert_eq!(fs::read_dir(directory.path()).unwrap().count(), 1);
        }
    }

    #[test]
    fn training_rng_and_cooldown_survive_atomic_save_with_writer_lock_intact() {
        let c = Config::sample().unwrap();
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("pet.json");
        let store = Store::open(&path).unwrap();
        let mut game = Game::new(&c.game, 371);
        for _ in 0..c.game.hatch_ticks {
            game.tick(&c.game);
        }
        game.train(4, &c.game).unwrap();
        store.save(&game).unwrap();
        let bytes = fs::read(&path).unwrap();
        let mut resumed = load(&path).unwrap().unwrap();
        assert_eq!(game, resumed);
        assert!(resumed.train(4, &c.game).unwrap_err().contains("cooldown"));
        assert_eq!(game, resumed);
        assert_eq!(fs::read(&path).unwrap(), bytes);
        assert!(Store::open(&path).is_err());
        game.tick(&c.game);
        resumed.tick(&c.game);
        assert_eq!(game.train(4, &c.game), resumed.train(4, &c.game));
        assert_eq!(game, resumed);
        let mut invalid = game.clone();
        invalid.progress.rng = 0;
        assert!(store.save(&invalid).is_err());
        assert_eq!(fs::read(&path).unwrap(), bytes);
        assert!(Store::open(&path).is_err());
    }
}
