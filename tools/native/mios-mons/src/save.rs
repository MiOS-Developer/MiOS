// AI-hint: Versioned, bounded per-user saves with an OS-held single-writer lock and atomic durable replacement.
// AI-related: game.rs, main.rs, usr/share/doc/mios/mios-mons.md
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

const DEFAULT_DIRECTORY: &str = "mios/mons";
// Exact historical default only: arbitrary operator paths are never rewritten.
const LEGACY_DEFAULT_DIRECTORY: &str = "mios/tamagotchi";

fn state_base() -> Result<Option<PathBuf>, String> {
    let base = env::var_os("XDG_STATE_HOME")
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
        .or_else(|| {
            env::var_os("HOME")
                .or_else(|| env::var_os("USERPROFILE"))
                .filter(|v| !v.is_empty())
                .map(|v| PathBuf::from(v).join(".local/state"))
        });
    let Some(base) = base else { return Ok(None) };
    if !base.is_absolute() {
        return Err("State home must be absolute; use --save PATH or --sample".into());
    }
    Ok(Some(base))
}

fn default_location(directory: &str) -> Result<(PathBuf, PathBuf), String> {
    let base = state_base()?.ok_or("No state home: use --save PATH, --ephemeral or --sample")?;
    let path = base.join(directory).join("pet.json");
    Ok((base, path))
}

/// A missing state home is valid for a redirected read-only snapshot; an
/// invalid/colliding configured store remains an error rather than a new pet.
pub fn available_default_path(directory: &str) -> Result<Option<PathBuf>, String> {
    if state_base()?.is_none() {
        return Ok(None);
    }
    default_path(directory).map(Some)
}

/// A snapshot resolves an existing default store without creating anything.
pub fn default_path(directory: &str) -> Result<PathBuf, String> {
    let (base, path) = default_location(directory)?;
    if directory == DEFAULT_DIRECTORY || directory == LEGACY_DEFAULT_DIRECTORY {
        #[cfg(target_os = "linux")]
        return default_store::read_path(&base.join("mios"));
        #[cfg(not(target_os = "linux"))]
        return portable_default_path(&base);
    }
    Ok(path)
}

#[cfg(not(target_os = "linux"))]
fn portable_default_path(base: &Path) -> Result<PathBuf, String> {
    let old = base.join(LEGACY_DEFAULT_DIRECTORY);
    let new = base.join(DEFAULT_DIRECTORY);
    match (fs::symlink_metadata(&old), fs::symlink_metadata(&new)) {
        (Ok(_), Ok(_)) => {
            Err("Default MiOS-Mons stores collide; preserve both and review their paths".into())
        }
        (Ok(meta), Err(e)) if meta.is_dir() && e.kind() == std::io::ErrorKind::NotFound => {
            Ok(old.join("pet.json"))
        }
        (Err(e), Ok(meta)) if e.kind() == std::io::ErrorKind::NotFound && meta.is_dir() => {
            Ok(new.join("pet.json"))
        }
        (Err(a), Err(b))
            if a.kind() == std::io::ErrorKind::NotFound
                && b.kind() == std::io::ErrorKind::NotFound =>
        {
            Ok(new.join("pet.json"))
        }
        _ => Err("Default MiOS-Mons store is unavailable or has an unexpected link".into()),
    }
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
            game.validate_habitat(rules)?;
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
    /// Only a configured default uses the canonicalization path. Explicit save
    /// paths and custom state directories retain their existing semantics.
    pub fn open_default(directory: &str) -> Result<Self, String> {
        let (base, path) = default_location(directory)?;
        if directory == DEFAULT_DIRECTORY || directory == LEGACY_DEFAULT_DIRECTORY {
            #[cfg(target_os = "linux")]
            return Self::open(&default_store::prepare(&base.join("mios"))?);
            #[cfg(not(target_os = "linux"))]
            return Self::open(&portable_default_path(&base)?);
        }
        Self::open(&path)
    }

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

/// Linux can promote the complete default directory atomically while an older
/// process holds a save lock or persists through the historical pathname.
#[cfg(target_os = "linux")]
mod default_store {
    use super::*;
    use rustix::fs::{self as at, AtFlags, FileType, Mode, OFlags, RenameFlags, Stat};
    use rustix::io::Errno;
    use std::os::unix::fs::{DirBuilderExt, MetadataExt};

    const OLD: &str = "tamagotchi";
    const NEW: &str = "mons";
    const MIGRATION_LOCK: &str = ".mons-migration.lock";

    fn inspect(parent: &File, name: &str) -> Result<Option<Stat>, String> {
        match at::statat(parent, name, AtFlags::SYMLINK_NOFOLLOW) {
            Ok(meta) => Ok(Some(meta)),
            Err(Errno::NOENT) => Ok(None),
            Err(e) => Err(format!("inspect default MiOS-Mons store: {e}")),
        }
    }

    fn owned_directory(meta: &Stat) -> bool {
        FileType::from_raw_mode(meta.st_mode) == FileType::Directory
            && meta.st_uid == rustix::process::geteuid().as_raw()
            && meta.st_mode & 0o022 == 0
    }

    fn same_object(a: &Stat, b: &Stat) -> bool {
        a.st_dev == b.st_dev
            && a.st_ino == b.st_ino
            && a.st_uid == b.st_uid
            && a.st_mode == b.st_mode
    }

    fn alias(parent: &File, name: &str, meta: &Stat, target: &str) -> bool {
        FileType::from_raw_mode(meta.st_mode) == FileType::Symlink
            && meta.st_uid == rustix::process::geteuid().as_raw()
            && at::readlinkat(parent, name, Vec::new())
                .is_ok_and(|actual| actual.as_bytes() == target.as_bytes())
    }

    fn open_parent(path: &Path) -> Result<Option<File>, String> {
        let fd = match at::open(
            path,
            OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW | OFlags::CLOEXEC,
            Mode::empty(),
        ) {
            Ok(fd) => File::from(fd),
            Err(Errno::NOENT) => return Ok(None),
            Err(e) => return Err(format!("open default MiOS-Mons parent: {e}")),
        };
        let meta = fd.metadata().map_err(|e| e.to_string())?;
        if !meta.is_dir()
            || meta.uid() != rustix::process::geteuid().as_raw()
            || meta.mode() & 0o022 != 0
        {
            return Err("Default MiOS-Mons parent must be an owned, private real directory".into());
        }
        parent_still_named(path, &fd)?;
        Ok(Some(fd))
    }

    fn parent_still_named(path: &Path, parent: &File) -> Result<(), String> {
        let named = fs::symlink_metadata(path).map_err(|e| e.to_string())?;
        let opened = parent.metadata().map_err(|e| e.to_string())?;
        if !named.is_dir()
            || named.dev() != opened.dev()
            || named.ino() != opened.ino()
            || named.uid() != opened.uid()
            || named.mode() != opened.mode()
        {
            return Err("Default MiOS-Mons parent changed during migration".into());
        }
        Ok(())
    }

    fn selected(parent: &File) -> Result<&'static str, String> {
        let old = inspect(parent, OLD)?;
        let new = inspect(parent, NEW)?;
        match (old.as_ref(), new.as_ref()) {
            (None, None) => Ok(NEW),
            (Some(old), None) if owned_directory(old) => Ok(OLD),
            (None, Some(new)) if owned_directory(new) => Ok(NEW),
            (Some(old), Some(new)) if owned_directory(new) && alias(parent, OLD, old, NEW) => Ok(NEW),
            (Some(old), Some(new)) if owned_directory(old) && alias(parent, NEW, new, OLD) => Ok(NEW),
            _ => Err("Default MiOS-Mons paths collide or migration is incomplete; preserve both entries for review".into()),
        }
    }

    pub(super) fn read_path(path: &Path) -> Result<PathBuf, String> {
        let Some(parent) = open_parent(path)? else {
            return Ok(path.join(NEW).join("pet.json"));
        };
        let name = selected(&parent)?;
        parent_still_named(path, &parent)?;
        Ok(path.join(name).join("pet.json"))
    }

    fn remove_prepared(parent: &File, prepared: &Stat) -> Result<(), String> {
        let current = inspect(parent, NEW)?.ok_or("Prepared MiOS-Mons link disappeared")?;
        if !same_object(&current, prepared) || !alias(parent, NEW, &current, NEW) {
            return Err("Prepared MiOS-Mons link changed; refusing cleanup".into());
        }
        at::unlinkat(parent, NEW, AtFlags::empty()).map_err(|e| e.to_string())
    }

    pub(super) fn prepare(path: &Path) -> Result<PathBuf, String> {
        if open_parent(path)?.is_none() {
            fs::DirBuilder::new()
                .recursive(true)
                .mode(0o700)
                .create(path)
                .map_err(|e| format!("create default MiOS-Mons parent: {e}"))?;
        }
        let parent = open_parent(path)?.ok_or("Default MiOS-Mons parent disappeared")?;
        // In particular, a collision must not even create a migration guard.
        selected(&parent)?;
        let guard = File::from(
            at::openat(
                &parent,
                MIGRATION_LOCK,
                OFlags::RDWR | OFlags::CREATE | OFlags::NOFOLLOW | OFlags::CLOEXEC,
                Mode::from_raw_mode(0o600),
            )
            .map_err(|e| format!("open MiOS-Mons migration guard: {e}"))?,
        );
        let guard_meta = guard.metadata().map_err(|e| e.to_string())?;
        if !guard_meta.is_file()
            || guard_meta.uid() != rustix::process::geteuid().as_raw()
            || guard_meta.mode() & 0o022 != 0
            || guard_meta.nlink() != 1
        {
            return Err("MiOS-Mons migration guard is not an owned private regular file".into());
        }
        guard
            .try_lock()
            .map_err(|_| "MiOS-Mons default migration is already in progress".to_string())?;
        parent_still_named(path, &parent)?;
        // Refuse all collisions before preparing any directory/link entry.
        let selection = selected(&parent)?;
        let old = inspect(&parent, OLD)?;
        let new = inspect(&parent, NEW)?;
        if old.is_none() && new.is_none() {
            at::mkdirat(&parent, NEW, Mode::from_raw_mode(0o700)).map_err(|e| e.to_string())?;
            at::symlinkat(NEW, &parent, OLD)
                .map_err(|e| format!("preserve legacy default path: {e}"))?;
            parent.sync_all().map_err(|e| e.to_string())?;
        } else if old.is_none() {
            // A canonical store may already exist without its compatibility
            // name. Establish the SAME lock namespace before a writer opens;
            // otherwise an older executable could create an independent store.
            let original = new
                .as_ref()
                .ok_or("Canonical default directory disappeared")?;
            let current =
                inspect(&parent, NEW)?.ok_or("Canonical default directory disappeared")?;
            if !owned_directory(&current) || !same_object(original, &current) {
                return Err(
                    "Canonical MiOS-Mons store changed before preserving its legacy alias".into(),
                );
            }
            // symlinkat creates exclusively: a racing/unknown legacy entry is
            // never overwritten, removed or adopted as this store.
            at::symlinkat(NEW, &parent, OLD)
                .map_err(|e| format!("preserve legacy default path: {e}"))?;
            let historical = inspect(&parent, OLD)?.ok_or("Legacy default alias disappeared")?;
            let after = inspect(&parent, NEW)?.ok_or("Canonical default directory disappeared")?;
            if !same_object(original, &after) || !alias(&parent, OLD, &historical, NEW) {
                return Err("MiOS-Mons legacy alias created but identity verification failed; preserve both paths".into());
            }
            // The alias is now live. A durability failure must not unlink it
            // or reopen a split between old and canonical writer locks.
            parent.sync_all().map_err(|e| {
                format!("MiOS-Mons legacy alias created; parent durability check failed: {e}")
            })?;
        } else if selection == OLD && new.is_none() {
            let original = old.as_ref().ok_or("Legacy default directory disappeared")?;
            at::symlinkat(NEW, &parent, NEW).map_err(|e| e.to_string())?;
            let prepared = inspect(&parent, NEW)?.ok_or("Prepared MiOS-Mons link disappeared")?;
            let current_old =
                inspect(&parent, OLD)?.ok_or("Legacy default directory disappeared")?;
            if !same_object(original, &current_old) || !alias(&parent, NEW, &prepared, NEW) {
                // Never guess whether another actor's entry may be removed.
                return Err(
                    "Default MiOS-Mons entries changed before exchange; preserve them for review"
                        .into(),
                );
            }
            match at::renameat_with(&parent, OLD, &parent, NEW, RenameFlags::EXCHANGE) {
                Ok(()) => {
                    // Exchange has committed. No subsequent error may remove
                    // the legacy alias or roll back an already live store.
                    let after =
                        inspect(&parent, NEW)?.ok_or("Canonical default directory disappeared")?;
                    let historical =
                        inspect(&parent, OLD)?.ok_or("Legacy default alias disappeared")?;
                    if !same_object(original, &after)
                        || !same_object(&prepared, &historical)
                        || !alias(&parent, OLD, &historical, NEW)
                    {
                        return Err("MiOS-Mons exchange committed but identity verification failed; preserve both paths".into());
                    }
                    parent.sync_all().map_err(|e| {
                        format!("MiOS-Mons exchange committed; parent durability check failed: {e}")
                    })?;
                }
                Err(error) => {
                    remove_prepared(&parent, &prepared)?;
                    let still_old =
                        inspect(&parent, OLD)?.ok_or("Legacy default directory disappeared")?;
                    if !same_object(original, &still_old) {
                        return Err("Legacy default directory changed after failed exchange".into());
                    }
                    if matches!(error, Errno::NOSYS | Errno::INVAL | Errno::OPNOTSUPP) {
                        // Unsupported exchange retains the original directory;
                        // both names still resolve the same writer-lock inode.
                        at::symlinkat(OLD, &parent, NEW).map_err(|e| e.to_string())?;
                        parent.sync_all().map_err(|e| e.to_string())?;
                    } else {
                        return Err(format!("MiOS-Mons default exchange failed without moving the existing store: {error}"));
                    }
                }
            }
        }
        let name = selected(&parent)?;
        parent_still_named(path, &parent)?;
        Ok(path.join(name).join("pet.json"))
    }
}

#[cfg(all(test, target_os = "linux"))]
mod canonical_default_tests {
    use super::*;
    use std::os::unix::fs::{MetadataExt, PermissionsExt};

    fn identity(path: &Path) -> (u64, u64) {
        let meta = fs::metadata(path).unwrap();
        (meta.dev(), meta.ino())
    }

    fn fixture() -> (tempfile::TempDir, PathBuf, PathBuf) {
        let temp = tempfile::tempdir().unwrap();
        let parent = temp.path().join("mios");
        let old = parent.join("tamagotchi");
        fs::create_dir_all(&old).unwrap();
        fs::set_permissions(&parent, fs::Permissions::from_mode(0o700)).unwrap();
        fs::set_permissions(&old, fs::Permissions::from_mode(0o700)).unwrap();
        (temp, parent, old)
    }

    type EntrySnapshot = (std::ffi::OsString, u64, u64, u32, Option<PathBuf>);

    fn entries(path: &Path) -> Vec<EntrySnapshot> {
        let mut rows = fs::read_dir(path)
            .unwrap()
            .map(|entry| {
                let entry = entry.unwrap();
                let meta = fs::symlink_metadata(entry.path()).unwrap();
                (
                    entry.file_name(),
                    meta.dev(),
                    meta.ino(),
                    meta.mode(),
                    fs::read_link(entry.path()).ok(),
                )
            })
            .collect::<Vec<_>>();
        rows.sort();
        rows
    }

    #[test]
    fn atomic_default_exchange_keeps_live_store_and_both_writer_locks_identical() {
        let (_temp, parent, old) = fixture();
        let path = old.join("pet.json");
        let config = crate::config::Config::sample().unwrap();
        let mut game = Game::new(&config.game, 515);
        game.coins = 42;
        let store = Store::open(&path).unwrap();
        store.save_with_rules(&game, &config.habitat).unwrap();
        fs::write(old.join("operator-extra"), b"preserve\0me").unwrap();
        let before = fs::read(&path).unwrap();
        let ids = [
            identity(&old),
            identity(&path),
            identity(&old.join("pet.json.lock")),
            identity(&old.join("operator-extra")),
        ];
        let new_path = default_store::prepare(&parent).unwrap();
        let new = parent.join("mons");
        assert!(!fs::symlink_metadata(&new).unwrap().file_type().is_symlink());
        assert_eq!(fs::read_link(&old).unwrap(), Path::new("mons"));
        assert_eq!(
            [
                identity(&new),
                identity(&new_path),
                identity(&new.join("pet.json.lock")),
                identity(&new.join("operator-extra"))
            ],
            ids
        );
        assert_eq!(fs::read(&path).unwrap(), before);
        assert_eq!(fs::read(&new_path).unwrap(), before);
        assert!(Store::open(&path).is_err());
        assert!(Store::open(&new_path).is_err());
        game.coins += 1;
        // A pre-existing Store still persists and syncs through its old path.
        store.save_with_rules(&game, &config.habitat).unwrap();
        assert_eq!(
            load_with_rules(&new_path, &config.habitat)
                .unwrap()
                .unwrap(),
            game
        );
        assert_eq!(identity(&old.join("pet.json.lock")), ids[2]);
        let state = entries(&parent);
        assert_eq!(default_store::prepare(&parent).unwrap(), new_path);
        assert_eq!(entries(&parent), state);
    }

    #[test]
    fn new_only_canonical_store_keeps_legacy_and_canonical_writers_on_the_same_lock() {
        let (_temp, parent, old) = fixture();
        let new = parent.join("mons");
        fs::rename(&old, &new).unwrap();
        let new_path = new.join("pet.json");
        let old_path = old.join("pet.json");
        let config = crate::config::Config::sample().unwrap();
        let mut game = Game::new(&config.game, 515);
        let writer = Store::open(&new_path).unwrap();
        writer.save_with_rules(&game, &config.habitat).unwrap();
        let original = [
            identity(&new),
            identity(&new_path),
            identity(&new.join("pet.json.lock")),
        ];
        let bytes = fs::read(&new_path).unwrap();
        // A snapshot still does not prepare the absent historical name.
        assert_eq!(default_store::read_path(&parent).unwrap(), new_path);
        assert!(fs::symlink_metadata(&old).is_err());
        assert_eq!(default_store::prepare(&parent).unwrap(), new_path);
        assert_eq!(fs::read_link(&old).unwrap(), Path::new("mons"));
        assert_eq!(
            [
                identity(&old),
                identity(&old_path),
                identity(&old.join("pet.json.lock"))
            ],
            original
        );
        assert_eq!(fs::read(&old_path).unwrap(), bytes);
        assert!(Store::open(&old_path).is_err());
        assert!(Store::open(&new_path).is_err());
        game.coins += 1;
        writer.save_with_rules(&game, &config.habitat).unwrap();
        assert_eq!(
            load_with_rules(&old_path, &config.habitat)
                .unwrap()
                .unwrap(),
            game
        );
        assert_eq!(identity(&old.join("pet.json.lock")), original[2]);
        let state = entries(&parent);
        assert_eq!(default_store::prepare(&parent).unwrap(), new_path);
        assert_eq!(entries(&parent), state);
        drop(writer);
        // A real legacy Store now acquires the original shared lock, and a
        // competing canonical Store refuses in the reverse direction too.
        let _legacy_writer = Store::open(&old_path).unwrap();
        assert!(Store::open(&new_path).is_err());
    }

    #[test]
    fn read_only_default_resolution_never_migrates_or_creates_a_lock() {
        let (_temp, parent, old) = fixture();
        let config = crate::config::Config::sample().unwrap();
        let game = Game::new(&config.game, 91);
        let store = Store::open(&old.join("pet.json")).unwrap();
        store.save_with_rules(&game, &config.habitat).unwrap();
        let before = entries(&parent);
        let bytes = fs::read(old.join("pet.json")).unwrap();
        let selected = default_store::read_path(&parent).unwrap();
        assert_eq!(selected, old.join("pet.json"));
        assert_eq!(
            load_with_rules(&selected, &config.habitat)
                .unwrap()
                .unwrap(),
            game
        );
        assert_eq!(entries(&parent), before);
        assert_eq!(fs::read(&selected).unwrap(), bytes);
        assert!(!parent.join(".mons-migration.lock").exists());
        assert!(fs::symlink_metadata(parent.join("mons")).is_err());
    }

    #[test]
    fn occupied_names_and_unowned_shape_refuse_without_changing_entries() {
        for collision in [
            "directory",
            "file",
            "self-link",
            "dangling-link",
            "existing-safe-alias",
        ] {
            let (_temp, parent, old) = fixture();
            let new = parent.join("mons");
            match collision {
                "directory" => fs::create_dir(&new).unwrap(),
                "file" => fs::write(&new, b"independent state").unwrap(),
                "self-link" => std::os::unix::fs::symlink("mons", &new).unwrap(),
                "dangling-link" => std::os::unix::fs::symlink("missing", &new).unwrap(),
                "existing-safe-alias" => std::os::unix::fs::symlink("tamagotchi", &new).unwrap(),
                _ => unreachable!(),
            }
            let before = entries(&parent);
            if collision == "existing-safe-alias" {
                // A previous safe fallback is retained, not physically promoted.
                assert_eq!(
                    default_store::prepare(&parent).unwrap(),
                    new.join("pet.json")
                );
                assert!(new.is_symlink());
                assert!(old.is_dir());
                assert_eq!(identity(&old), identity(&new));
            } else {
                assert!(default_store::read_path(&parent).is_err());
                assert!(default_store::prepare(&parent).is_err());
                assert_eq!(entries(&parent), before);
            }
        }
        let (_temp, parent, old) = fixture();
        fs::rename(&old, parent.join("operator-directory")).unwrap();
        std::os::unix::fs::symlink("operator-directory", &old).unwrap();
        let before = entries(&parent);
        assert!(default_store::prepare(&parent).is_err());
        assert_eq!(entries(&parent), before);
    }

    #[test]
    fn explicit_store_and_in_flight_old_path_persist_preserve_their_namespace() {
        let (_temp, parent, old) = fixture();
        let path = old.join("pet.json");
        let _store = Store::open(&path).unwrap();
        // Explicit Store::open does not infer a migration from the filename.
        assert!(fs::symlink_metadata(parent.join("mons")).is_err());
        let lock_id = identity(&old.join("pet.json.lock"));
        let mut pending = tempfile::NamedTempFile::new_in(&old).unwrap();
        pending.write_all(b"saved before exchange").unwrap();
        pending.as_file().sync_all().unwrap();
        let pending_id = identity(pending.path());
        let selected = default_store::prepare(&parent).unwrap();
        pending.persist(&path).unwrap();
        assert_eq!(identity(&path), pending_id);
        assert_eq!(identity(&selected), pending_id);
        assert_eq!(fs::read(&selected).unwrap(), b"saved before exchange");
        assert_eq!(identity(&old.join("pet.json.lock")), lock_id);
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

// Complete test module for tools/native/mios-mons/src/save.rs.
// Run only in real MiOS-DEV. Guards validation before legacy progression replacement.
#[cfg(test)]
mod supplied_legacy_progression_review {
    use super::*;
    use crate::config::Config;

    #[test]
    fn supplied_future_training_counter_is_refused_in_every_schema_without_rewrites() {
        let config = Config::sample().unwrap();
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("pet.json");
        let mut game = Game::new(&config.game, 441);
        for _ in 0..config.game.hatch_ticks {
            game.tick(&config.game);
        }
        game.train(0, &config.game).unwrap();
        game.progress.last_train_tick = Some(game.age_ticks + 1);
        assert!(game
            .validate_habitat(&config.habitat)
            .unwrap_err()
            .contains("future training tick"));
        for schema in 1..=4 {
            let bytes =
                serde_json::to_vec(&serde_json::json!({"schema":schema,"game":game})).unwrap();
            fs::write(&path, &bytes).unwrap();
            let error = load_with_rules(&path, &config.habitat).unwrap_err();
            assert!(
                error.contains("future training tick"),
                "schema{schema}: {error}"
            );
            assert_eq!(fs::read(&path).unwrap(), bytes);
            assert_eq!(fs::read_dir(directory.path()).unwrap().count(), 1);
        }
    }
}
