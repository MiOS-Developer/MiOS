// AI-hint: Native bootstrap mirror with parsed TOML comparisons, confined paths and rollback on publication failure.
// AI-related: tools/sync-bootstrap.py, usr/share/mios/mios.toml, automation/98-drift-checks.sh

use serde::Deserialize;
use std::collections::BTreeSet;
use std::fs;
use std::io::Write;
use std::path::{Component, Path, PathBuf};
use std::process::Command;
use tempfile::NamedTempFile;
use toml_edit::{Document as DocumentMut, Item, Table};

type Result<T> = std::result::Result<T, String>;

#[derive(Deserialize)]
struct Manifest {
    mirror_files: Vec<String>,
    #[serde(default)]
    mirror_toml_tables: Vec<String>,
    #[serde(default)]
    mirror_toml_keys: Vec<String>,
    #[serde(default)]
    not_mirrored: Vec<String>,
}

struct Change {
    path: PathBuf,
    bytes: Vec<u8>,
    previous: Option<Vec<u8>>,
    permissions: fs::Permissions,
}

fn read(path: &Path) -> Result<Vec<u8>> {
    fs::read(path).map_err(|e| format!("{}: {e}", path.display()))
}

fn parse(bytes: &[u8]) -> Result<toml::Value> {
    std::str::from_utf8(bytes)
        .map_err(|e| e.to_string())?
        .parse()
        .map_err(|e: toml::de::Error| e.to_string())
}

fn parts(value: &str) -> Result<Vec<&str>> {
    let parts: Vec<_> = value.split('.').collect();
    if parts.iter().any(|p| p.is_empty() || p.trim() != *p) {
        return Err(format!("malformed TOML path: {value:?}"));
    }
    Ok(parts)
}

// Reject Windows separators and drive syntax on Linux too, keeping manifests portable.
fn confined(root: &Path, relative: &str, missing: bool) -> Result<PathBuf> {
    if relative.is_empty()
        || relative.trim() != relative
        || relative.contains(['\\', ':'])
        || Path::new(relative)
            .components()
            .any(|p| !matches!(p, Component::Normal(_)))
        || relative
            .split('/')
            .any(|p| p.is_empty() || p == "." || p == "..")
    {
        return Err(format!("invalid mirror path: {relative:?}"));
    }
    let mut path = root.to_path_buf();
    for component in Path::new(relative).components() {
        path.push(component);
        match fs::symlink_metadata(&path) {
            Ok(meta) if meta.file_type().is_symlink() => {
                return Err(format!(
                    "mirror path contains a symlink: {}",
                    path.display()
                ))
            }
            Ok(_) => {}
            Err(e) if missing && e.kind() == std::io::ErrorKind::NotFound => {}
            Err(e) => return Err(format!("{}: {e}", path.display())),
        }
    }
    Ok(path)
}

fn tracked(root: &Path) -> Result<BTreeSet<String>> {
    let git_root = root.to_string_lossy();
    let git_root = git_root.strip_prefix(r"\\?\").unwrap_or(&git_root);
    let output = Command::new("git")
        .arg("-C")
        .arg(git_root)
        .args(["ls-files", "-z"])
        .output()
        .map_err(|e| format!("tracked scan: {e}"))?;
    if !output.status.success() || output.stdout.is_empty() {
        return Err(format!(
            "tracked scan failed or empty at {}",
            root.display()
        ));
    }
    Ok(std::str::from_utf8(&output.stdout)
        .map_err(|e| e.to_string())?
        .split('\0')
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .collect())
}

fn table<'a>(
    value: &'a toml::Value,
    path: &[&str],
) -> Result<&'a toml::map::Map<String, toml::Value>> {
    let mut value = value;
    for part in path {
        value = value
            .get(*part)
            .ok_or_else(|| format!("missing authority table {}", path.join(".")))?;
    }
    value
        .as_table()
        .ok_or_else(|| format!("expected table {}", path.join(".")))
}

fn edit_table<'a>(value: &'a mut Table, path: &[&str]) -> Result<&'a mut Table> {
    let Some((first, rest)) = path.split_first() else {
        return Ok(value);
    };
    if !value.contains_key(first) {
        value.insert(first, Item::Table(Table::new()));
    }
    let nested = value
        .get_mut(first)
        .and_then(Item::as_table_mut)
        .ok_or_else(|| format!("destination table conflicts with scalar {first}"))?;
    edit_table(nested, rest)
}

fn source_item(doc: &DocumentMut, path: &[&str], key: &str) -> Result<Item> {
    let mut item: &Item = doc.as_item();
    for part in path {
        item = item
            .get(part)
            .ok_or_else(|| format!("missing authority {part}"))?;
    }
    item.get(key)
        .cloned()
        .ok_or_else(|| format!("missing authority key {key}"))
}

fn put(target: &mut Table, key: &str, mut item: Item) {
    if let (Some(old), Some(new)) = (
        target.get(key).and_then(Item::as_value),
        item.as_value_mut(),
    ) {
        *new.decor_mut() = old.decor().clone();
    }
    target.insert(key, item);
}

fn normalized(bytes: &[u8]) -> Vec<u8> {
    bytes
        .iter()
        .enumerate()
        .filter_map(|(i, b)| {
            if *b == b'\r' && bytes.get(i + 1) == Some(&b'\n') {
                None
            } else {
                Some(*b)
            }
        })
        .collect()
}

fn stage(change: &Change, bytes: &[u8]) -> Result<NamedTempFile> {
    let parent = change.path.parent().ok_or("destination has no parent")?;
    fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    let mut tmp = NamedTempFile::new_in(parent).map_err(|e| e.to_string())?;
    tmp.as_file()
        .set_permissions(change.permissions.clone())
        .map_err(|e| e.to_string())?;
    tmp.write_all(bytes).map_err(|e| e.to_string())?;
    tmp.as_file().sync_all().map_err(|e| e.to_string())?;
    Ok(tmp)
}

fn publish_with<F>(changes: &[Change], mut persist: F) -> Result<()>
where
    F: FnMut(usize, NamedTempFile, &Path) -> Result<()>,
{
    // Stage every replacement and restoration before publishing any target.
    let mut pending = Vec::new();
    let mut backups = Vec::new();
    for change in changes {
        pending.push(stage(change, &change.bytes)?);
        backups.push(
            change
                .previous
                .as_ref()
                .map(|old| stage(change, old))
                .transpose()?,
        );
    }
    for (index, (change, tmp)) in changes.iter().zip(pending).enumerate() {
        if let Err(error) = persist(index, tmp, &change.path) {
            let mut failures = Vec::new();
            for prior in (0..index).rev() {
                let result = match backups[prior].take() {
                    Some(backup) => backup
                        .persist(&changes[prior].path)
                        .map(|_| ())
                        .map_err(|e| e.to_string()),
                    None => fs::remove_file(&changes[prior].path).map_err(|e| e.to_string()),
                };
                if let Err(e) = result {
                    failures.push(e);
                }
            }
            return Err(format!(
                "publication failed: {error}; rollback errors: {failures:?}"
            ));
        }
    }
    Ok(())
}

pub fn run(root: &Path, bootstrap: &Path, apply: bool) -> Result<String> {
    let root = fs::canonicalize(root).map_err(|e| format!("authority root: {e}"))?;
    let boot = fs::canonicalize(bootstrap)
        .map_err(|e| format!("bootstrap repo absent: {e}; Law 15 NOT checked"))?;
    if root == boot {
        return Err("authority and bootstrap roots must differ".into());
    }
    let authority = read(&confined(&root, "usr/share/mios/mios.toml", false)?)?;
    let data = parse(&authority)?;
    let manifest: Manifest = data
        .get("bootstrap")
        .and_then(|v| v.get("sync"))
        .ok_or("missing [bootstrap.sync]")?
        .clone()
        .try_into()
        .map_err(|e: toml::de::Error| e.to_string())?;
    if manifest.mirror_files.is_empty() {
        return Err("mirror_files is empty; nothing would be compared".into());
    }
    let mut declared = BTreeSet::new();
    for name in manifest.mirror_files.iter().chain(&manifest.not_mirrored) {
        confined(&boot, name, true)?;
        if !declared.insert(name.clone()) {
            return Err(format!("duplicate/conflicting manifest path: {name}"));
        }
    }
    let shared = tracked(&root)?
        .intersection(&tracked(&boot)?)
        .cloned()
        .collect::<BTreeSet<_>>();
    let unclassified: Vec<_> = shared.difference(&declared).cloned().collect();
    if !unclassified.is_empty() {
        return Err(format!(
            "unclassified shared tracked files: {unclassified:?}"
        ));
    }
    let mut changes = Vec::new();
    for name in &manifest.mirror_files {
        let source = confined(&root, name, false)?;
        let path = confined(&boot, name, true)?;
        let bytes = read(&source)?;
        let previous = if path.exists() {
            Some(read(&path)?)
        } else {
            None
        };
        if previous.as_ref().map(|v| normalized(v)) != Some(normalized(&bytes)) {
            let permissions = fs::metadata(if path.exists() { &path } else { &source })
                .map_err(|e| e.to_string())?
                .permissions();
            changes.push(Change {
                path,
                bytes,
                previous,
                permissions,
            });
        }
    }
    if !manifest.mirror_toml_tables.is_empty() || !manifest.mirror_toml_keys.is_empty() {
        if manifest.mirror_files.iter().any(|s| s == "mios.toml") {
            return Err("mios.toml file/table mirror conflict".into());
        }
        let path = confined(&boot, "mios.toml", false)?;
        let previous = read(&path)?;
        let boot_data = parse(&previous)?;
        let mut doc: DocumentMut = std::str::from_utf8(&previous)
            .map_err(|e| e.to_string())?
            .parse()
            .map_err(|e: toml_edit::TomlError| e.to_string())?;
        let source_doc: DocumentMut = std::str::from_utf8(&authority)
            .map_err(|e| e.to_string())?
            .parse()
            .map_err(|e: toml_edit::TomlError| e.to_string())?;
        let mut drift = false;
        let mut scopes = BTreeSet::new();
        for name in &manifest.mirror_toml_tables {
            let path = parts(name)?;
            if !scopes.insert(name.clone()) {
                return Err(format!("duplicate table: {name}"));
            }
            let wanted = table(&data, &path)?;
            let current = table(&boot_data, &path).ok();
            let wanted_scalars: BTreeSet<_> = wanted
                .iter()
                .filter(|(_, v)| !v.is_table())
                .map(|(k, _)| k.clone())
                .collect();
            let current_scalars: BTreeSet<_> = current
                .into_iter()
                .flat_map(|t| t.iter())
                .filter(|(_, v)| !v.is_table())
                .map(|(k, _)| k.clone())
                .collect();
            let target = edit_table(doc.as_table_mut(), &path)?;
            for key in current_scalars.difference(&wanted_scalars) {
                target.remove(key);
                drift = true;
            }
            for key in wanted_scalars {
                if current.and_then(|t| t.get(&key)) != wanted.get(&key) {
                    if target.get(&key).is_some_and(Item::is_table) {
                        return Err(format!(
                            "destination key conflicts with table: {name}.{key}"
                        ));
                    }
                    put(target, &key, source_item(&source_doc, &path, &key)?);
                    drift = true;
                }
            }
        }
        for name in &manifest.mirror_toml_keys {
            let path = parts(name)?;
            if path.len() < 2 || !scopes.insert(name.clone()) {
                return Err(format!("malformed/duplicate key: {name}"));
            }
            let (key, parent) = path.split_last().ok_or("empty key")?;
            let wanted = table(&data, parent)?
                .get(*key)
                .ok_or_else(|| format!("missing authority key {name}"))?;
            if wanted.is_table() {
                return Err(format!("expected scalar/array key {name}"));
            }
            if table(&boot_data, parent).ok().and_then(|t| t.get(*key)) != Some(wanted) {
                let target = edit_table(doc.as_table_mut(), parent)?;
                if target.get(key).is_some_and(Item::is_table) {
                    return Err(format!("destination key conflicts with table: {name}"));
                }
                put(target, key, source_item(&source_doc, parent, key)?);
                drift = true;
            }
        }
        if drift {
            let bytes = doc.to_string().into_bytes();
            parse(&bytes)?;
            let permissions = fs::metadata(&path)
                .map_err(|e| e.to_string())?
                .permissions();
            changes.push(Change {
                path,
                bytes,
                previous: Some(previous),
                permissions,
            });
        }
    }
    if !changes.is_empty() && !apply {
        return Err(format!(
            "{} surface(s) drifted from mios.git: {}",
            changes.len(),
            changes
                .iter()
                .map(|c| c.path.display().to_string())
                .collect::<Vec<_>>()
                .join(", ")
        ));
    }
    if apply {
        publish_with(&changes, |_, tmp, path| {
            tmp.persist(path).map(|_| ()).map_err(|e| e.to_string())
        })?;
    }
    Ok(format!(
        "{} mirrored file(s), {} table(s), {} key(s) match; {} surface(s) applied",
        manifest.mirror_files.len(),
        manifest.mirror_toml_tables.len(),
        manifest.mirror_toml_keys.len(),
        changes.len()
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Fixture {
        _temp: tempfile::TempDir,
        root: PathBuf,
        boot: PathBuf,
    }
    impl Fixture {
        fn new(extra: &str) -> Self {
            let temp = tempfile::tempdir().unwrap();
            let root = temp.path().join("source");
            let boot = temp.path().join("bootstrap");
            fs::create_dir_all(root.join("usr/share/mios")).unwrap();
            fs::create_dir_all(&boot).unwrap();
            fs::write(
                root.join("usr/share/mios/mios.toml"),
                format!(
                    r#"
[bootstrap.sync]
mirror_files = ["VERSION"]
not_mirrored = ["mios.toml"]
mirror_toml_tables = ["ports"]
mirror_toml_keys = ["theme.padding"]
{extra}
[ports]
alpha = 9
label = """new
line"""
[theme]
padding = [1, 2]
unowned = "source"
"#
                ),
            )
            .unwrap();
            fs::write(root.join("VERSION"), "new\n").unwrap();
            fs::write(boot.join("VERSION"), "old\n").unwrap();
            fs::write(boot.join("mios.toml"), "# operator header\n[ports]\nalpha = 1 # keep comment\nlabel = 'old'\nobsolete = 3\n[ports.nested]\noperator = true\n[theme]\npadding = [0]\nunowned = 'operator'\n").unwrap();
            for repo in [&root, &boot] {
                assert!(Command::new("git")
                    .arg("-C")
                    .arg(repo)
                    .arg("init")
                    .output()
                    .unwrap()
                    .status
                    .success());
                assert!(Command::new("git")
                    .arg("-C")
                    .arg(repo)
                    .args(["add", "."])
                    .output()
                    .unwrap()
                    .status
                    .success());
            }
            Self {
                _temp: temp,
                root,
                boot,
            }
        }
        fn manifest(&self, from: &str, to: &str) {
            let path = self.root.join("usr/share/mios/mios.toml");
            fs::write(&path, fs::read_to_string(&path).unwrap().replace(from, to)).unwrap();
        }
        fn snapshot(&self) -> (Vec<u8>, Vec<u8>) {
            (
                read(&self.boot.join("VERSION")).unwrap(),
                read(&self.boot.join("mios.toml")).unwrap(),
            )
        }
    }

    #[test]
    fn check_is_read_only_and_apply_converges_preserving_operator_content() {
        let f = Fixture::new("");
        let before = f.snapshot();
        assert!(run(&f.root, &f.boot, false)
            .unwrap_err()
            .contains("drifted"));
        assert_eq!(f.snapshot(), before);
        assert!(run(&f.root, &f.boot, true).is_ok());
        assert!(run(&f.root, &f.boot, false).is_ok());
        let after = f.snapshot();
        let doc = std::str::from_utf8(&after.1).unwrap();
        assert!(doc.contains("# operator header"));
        assert!(doc.contains("# keep comment"));
        let data = parse(&after.1).unwrap();
        assert_eq!(data["ports"]["alpha"].as_integer(), Some(9));
        assert_eq!(data["ports"]["label"].as_str(), Some("new\nline"));
        assert!(data["ports"].get("obsolete").is_none());
        assert_eq!(data["ports"]["nested"]["operator"].as_bool(), Some(true));
        assert_eq!(data["theme"]["unowned"].as_str(), Some("operator"));
        run(&f.root, &f.boot, true).unwrap();
        assert_eq!(f.snapshot(), after);
    }

    #[test]
    fn malformed_paths_fail_before_any_write() {
        for bad in [
            "../outside",
            "/outside",
            "C:/outside",
            "a\\b",
            "a//b",
            "a/./b",
            " VERSION",
        ] {
            let f = Fixture::new("");
            f.manifest(
                "[\"VERSION\"]",
                &format!("[\"VERSION\", {}]", serde_json::to_string(bad).unwrap()),
            );
            let before = f.snapshot();
            assert!(
                run(&f.root, &f.boot, true)
                    .unwrap_err()
                    .contains("invalid mirror path"),
                "{bad}"
            );
            assert_eq!(f.snapshot(), before);
        }
    }

    #[test]
    fn missing_authority_and_bad_toml_scopes_fail_before_any_write() {
        for (from, to) in [
            ("[\"VERSION\"]", "[\"VERSION\", \"MISSING\"]"),
            ("theme.padding", "theme.missing"),
            ("theme.padding", "theme..padding"),
            ("[\"ports\"]", "[\"absent\"]"),
            ("[\"VERSION\"]", "[]"),
            ("[\"VERSION\"]", "[\"VERSION\", \"VERSION\"]"),
        ] {
            let f = Fixture::new("");
            f.manifest(from, to);
            let before = f.snapshot();
            assert!(run(&f.root, &f.boot, true).is_err(), "{to}");
            assert_eq!(f.snapshot(), before);
        }
    }

    #[test]
    fn invalid_destination_toml_fails_before_file_copy() {
        let f = Fixture::new("");
        fs::write(f.boot.join("mios.toml"), "[ports]\n[ports]\n").unwrap();
        let before = f.snapshot();
        assert!(run(&f.root, &f.boot, true).is_err());
        assert_eq!(f.snapshot(), before);
    }

    #[test]
    fn unclassified_and_empty_or_missing_indexes_fail_closed() {
        let f = Fixture::new("");
        for repo in [&f.root, &f.boot] {
            fs::write(repo.join("unclassified"), "tracked").unwrap();
            assert!(Command::new("git")
                .arg("-C")
                .arg(repo)
                .args(["add", "unclassified"])
                .status()
                .unwrap()
                .success());
        }
        let before = f.snapshot();
        assert!(run(&f.root, &f.boot, true)
            .unwrap_err()
            .contains("unclassified"));
        assert_eq!(f.snapshot(), before);
        let empty = tempfile::tempdir().unwrap();
        assert!(tracked(empty.path()).is_err());
        assert!(Command::new("git")
            .arg("-C")
            .arg(empty.path())
            .arg("init")
            .output()
            .unwrap()
            .status
            .success());
        assert!(tracked(empty.path()).is_err());
    }

    #[test]
    fn publication_failure_restores_existing_and_removes_new_targets() {
        let temp = tempfile::tempdir().unwrap();
        let old = temp.path().join("old");
        fs::write(&old, "original").unwrap();
        let permissions = fs::metadata(&old).unwrap().permissions();
        let changes = vec![
            Change {
                path: old.clone(),
                bytes: b"replacement".to_vec(),
                previous: Some(b"original".to_vec()),
                permissions: permissions.clone(),
            },
            Change {
                path: temp.path().join("new"),
                bytes: b"created".to_vec(),
                previous: None,
                permissions: permissions.clone(),
            },
            Change {
                path: temp.path().join("failed"),
                bytes: b"failure".to_vec(),
                previous: None,
                permissions,
            },
        ];
        let error = publish_with(&changes, |index, tmp, path| {
            if index == 2 {
                Err("injected failure".into())
            } else {
                tmp.persist(path).map(|_| ()).map_err(|e| e.to_string())
            }
        })
        .unwrap_err();
        assert!(error.contains("injected failure"));
        assert!(error.contains("rollback errors: []"));
        assert_eq!(read(&old).unwrap(), b"original");
        assert!(!changes[1].path.exists());
        assert!(!changes[2].path.exists());
        assert_eq!(fs::read_dir(temp.path()).unwrap().count(), 1);
    }

    #[test]
    fn newline_only_differences_are_not_drift() {
        let f = Fixture::new("");
        run(&f.root, &f.boot, true).unwrap();
        fs::write(f.boot.join("VERSION"), "new\r\n").unwrap();
        run(&f.root, &f.boot, false).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn source_and_destination_symlink_escapes_are_rejected() {
        use std::os::unix::fs::symlink;
        for source in [true, false] {
            let f = Fixture::new("");
            let victim = f._temp.path().join("victim");
            fs::write(&victim, "safe").unwrap();
            let path = if source {
                f.root.join("VERSION")
            } else {
                f.boot.join("VERSION")
            };
            fs::remove_file(&path).unwrap();
            symlink(&victim, &path).unwrap();
            assert!(run(&f.root, &f.boot, true).unwrap_err().contains("symlink"));
            assert_eq!(read(&victim).unwrap(), b"safe");
        }
    }
}
