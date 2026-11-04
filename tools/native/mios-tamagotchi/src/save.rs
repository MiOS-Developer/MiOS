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

const SCHEMA: u32 = 1;
const MAX_SAVE_BYTES: u64 = 65_536;

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Envelope {
    schema: u32,
    game: Game,
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
    let envelope: Envelope = serde_json::from_slice(&bytes)
        .map_err(|e| format!("invalid save {}: {e}", path.display()))?;
    if envelope.schema != SCHEMA {
        return Err(format!("unsupported save schema {}", envelope.schema));
    }
    envelope.game.validate()?;
    // No elapsed wall-clock time is applied. Offline time, including clock reversal, is frozen.
    Ok(Some(envelope.game))
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
    pub fn save(&self, game: &Game) -> Result<(), String> {
        game.validate()?;
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
