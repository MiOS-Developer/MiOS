// AI-hint: mios-controls apply: writes a validated draft into the user tier atomically under an exclusive lock -- refuses when the file changed since it was read, when anything outside [keybindings] would change, or when the result would be empty; keeps a private backup of the previous tier.
// AI-related: tools/native/mios-controls/src/model.rs, tools/native/mios-resolver/src/layers.rs (user_toml_path)
// AI-functions: apply
use crate::model::{parse_value, without_keybindings, Layers};
use std::fs::{File, OpenOptions};
use std::io::Write;
use std::os::unix::fs::OpenOptionsExt;
use std::path::Path;

fn private_write(path: &Path, bytes: &[u8]) -> Result<(), String> {
    let mut f = OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .mode(0o600)
        .open(path)
        .map_err(|e| format!("{}: {e}", path.display()))?;
    f.write_all(bytes).map_err(|e| format!("{}: {e}", path.display()))?;
    f.sync_all().map_err(|e| format!("{}: {e}", path.display()))
}

/// Replace the user tier with `new_text`. Returns the backup path when a previous tier existed.
pub fn apply(layers: &Layers, new_text: &str, backup_suffix: &str) -> Result<Option<std::path::PathBuf>, String> {
    let path = &layers.user_path;
    let dir = path.parent().ok_or("the user tier has no parent directory")?;
    std::fs::create_dir_all(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    // Invariants on the rendered text before anything is written.
    let new_value = parse_value(new_text).map_err(|e| format!("the draft does not parse: {e}"))?;
    if without_keybindings(&new_value) != without_keybindings(&layers.user_value) {
        return Err("refusing: the draft would change tables outside [keybindings]".into());
    }
    // Emptying is legitimate when the tier held only [keybindings] (restore defaults); never when it held anything
    // else (defence in depth: the invariant above already proves those tables are unchanged).
    let others_empty = without_keybindings(&layers.user_value).as_table().is_none_or(|t| t.is_empty());
    if new_text.trim().is_empty() && !others_empty {
        return Err("refusing: the draft would empty a user tier that holds other settings".into());
    }
    let lock_path = dir.join(format!(".{}.lock", path.file_name().and_then(|n| n.to_str()).unwrap_or("mios.toml")));
    let lock = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .mode(0o600)
        .open(&lock_path)
        .map_err(|e| format!("{}: {e}", lock_path.display()))?;
    lock.lock().map_err(|e| format!("lock {}: {e}", lock_path.display()))?;
    let on_disk = match std::fs::read_to_string(path) {
        Ok(t) => t,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => String::new(),
        Err(e) => return Err(format!("{}: {e}", path.display())),
    };
    if on_disk != layers.user_text {
        return Err(format!("{} changed since it was read; reload the draft", path.display()));
    }
    let backup = if on_disk.is_empty() {
        None
    } else {
        let b = path.with_extension(format!("toml.{backup_suffix}"));
        private_write(&b, on_disk.as_bytes())?;
        Some(b)
    };
    let tmp = dir.join(format!(".mios-controls-{}.tmp", std::process::id()));
    private_write(&tmp, new_text.as_bytes())?;
    std::fs::rename(&tmp, path).map_err(|e| {
        let _ = std::fs::remove_file(&tmp);
        format!("rename into {}: {e}", path.display())
    })?;
    File::open(dir).and_then(|d| d.sync_all()).map_err(|e| format!("{}: {e}", dir.display()))?;
    let written = std::fs::read_to_string(path).map_err(|e| format!("{}: {e}", path.display()))?;
    if written != new_text {
        return Err(format!("{} does not hold the applied draft after the rename", path.display()));
    }
    drop(lock);
    Ok(backup)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;

    fn layers(dir: &Path, user: &str) -> Layers {
        let path = dir.join("cfg/mios/mios.toml");
        if !user.is_empty() {
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(&path, user).unwrap();
        }
        Layers {
            root: dir.into(),
            user_path: path,
            base: "[keybindings.global]\n".parse().unwrap(),
            user_text: user.into(),
            user_value: parse_value(user).unwrap(),
        }
    }

    #[test]
    fn applies_privately_with_backup() {
        let d = tempfile::tempdir().unwrap();
        let user = "[identity]\nsecret = \"s\"\n";
        let l = layers(d.path(), user);
        let new = format!("{user}\n[keybindings.global.controller.system_layer]\nBTN_WEST = \"exec:foot\"\n");
        let b = apply(&l, &new, "controls-backup").unwrap().unwrap();
        assert_eq!(std::fs::read_to_string(&l.user_path).unwrap(), new);
        assert_eq!(std::fs::read_to_string(&b).unwrap(), user);
        for p in [&l.user_path, &b] {
            assert_eq!(std::fs::metadata(p).unwrap().permissions().mode() & 0o777, 0o600);
        }
    }

    #[test]
    fn refuses_foreign_table_changes_emptying_and_concurrent_edits() {
        let d = tempfile::tempdir().unwrap();
        let user = "[identity]\nsecret = \"s\"\n";
        let l = layers(d.path(), user);
        assert!(apply(&l, "[identity]\nsecret = \"other\"\n", "b").unwrap_err().contains("outside [keybindings]"));
        assert!(apply(&l, "", "b").is_err());
        std::fs::write(&l.user_path, "[identity]\nsecret = \"s\"\n# edited elsewhere\n").unwrap();
        let new = format!("{user}[keybindings.global]\nenabled = true\n");
        assert!(apply(&l, &new, "b").unwrap_err().contains("changed since"));
        assert!(std::fs::read_to_string(&l.user_path).unwrap().contains("edited elsewhere"), "nothing overwritten");
    }

    #[test]
    fn restoring_a_keybindings_only_tier_empties_it() {
        let d = tempfile::tempdir().unwrap();
        let user = "[keybindings.global.controller.system_layer]\nBTN_WEST = \"exec:foot\"\n";
        let l = layers(d.path(), user);
        apply(&l, "", "b").unwrap();
        assert_eq!(std::fs::read_to_string(&l.user_path).unwrap(), "");
    }

    #[test]
    fn creates_a_missing_user_tier_without_backup() {
        let d = tempfile::tempdir().unwrap();
        let l = layers(d.path(), "");
        let new = "[keybindings.global.controller.system_layer]\nBTN_WEST = \"exec:foot\"\n";
        assert!(apply(&l, new, "b").unwrap().is_none());
        assert_eq!(std::fs::read_to_string(&l.user_path).unwrap(), new);
    }
}
