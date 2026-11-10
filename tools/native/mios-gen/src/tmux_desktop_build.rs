// AI-hint: Build verified upstream MiOS-Tmux source inside an admitted MiOS-DEV container, then atomically stage its binary and digest receipt.
// AI-related: usr/share/mios/mios.toml [keybindings.tmux.desktop.source], tools/patches/tmux-desktop.patch, tools/native/mios-gen/src/main.rs
// AI-functions: run, container_name, fresh_build_directory, stage
#![forbid(unsafe_code)]

use sha2::{Digest, Sha256};
use std::fs::{self, File};
use std::io::{Read, Write};
use std::os::unix::fs::{DirBuilderExt, MetadataExt, PermissionsExt};
use std::path::{Component, Path, PathBuf};
use std::process::{Command, Stdio};
use toml::Value;

fn field<'a>(v: &'a Value, name: &str) -> Result<&'a str, String> {
    v.get(name)
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty() && !s.chars().any(char::is_control))
        .ok_or_else(|| format!("desktop source {name} must be a nonempty string"))
}
fn normal_relative(path: &Path) -> bool {
    !path.as_os_str().is_empty() && path.components().all(|c| matches!(c, Component::Normal(_)))
}
fn absolute_directory(path: &Path) -> Result<(), String> {
    if path.to_str().is_none()
        || !path.is_absolute()
        || path == Path::new("/")
        || path
            .components()
            .skip(1)
            .any(|c| !matches!(c, Component::Normal(_)))
    {
        return Err(format!(
            "{} must be an absolute non-root path without traversal",
            path.display()
        ));
    }
    let mut part = PathBuf::from("/");
    for c in path.components().skip(1) {
        part.push(c);
        match fs::symlink_metadata(&part) {
            Ok(m) if m.is_dir() && !m.file_type().is_symlink() => (),
            Ok(_) => {
                return Err(format!(
                    "{} must be a directory without symlinks",
                    part.display()
                ))
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => (),
            Err(e) => return Err(format!("{}: {e}", part.display())),
        }
    }
    Ok(())
}
fn directories(path: &Path) -> Result<(), String> {
    absolute_directory(path)?;
    let mut part = PathBuf::from("/");
    for c in path.components().skip(1) {
        part.push(c);
        match fs::symlink_metadata(&part) {
            Ok(m) if m.is_dir() && !m.file_type().is_symlink() => (),
            Ok(_) => return Err(format!("unsafe directory {}", part.display())),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                fs::DirBuilder::new()
                    .mode(0o700)
                    .create(&part)
                    .map_err(|e| e.to_string())?;
            }
            Err(e) => return Err(e.to_string()),
        }
    }
    Ok(())
}
fn fresh_build_directory(path: &Path) -> Result<(), String> {
    absolute_directory(path)?;
    match fs::symlink_metadata(path) {
        Ok(m) => {
            if !m.is_dir()
                || m.file_type().is_symlink()
                || m.uid() != rustix::process::getuid().as_raw()
                || m.mode() & 0o077 != 0
                || fs::read_dir(path)
                    .map_err(|e| e.to_string())?
                    .next()
                    .is_some()
            {
                return Err(
                    "build directory must be fresh, empty, private, and caller-owned".into(),
                );
            }
        }
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            absolute_directory(path.parent().ok_or("build directory has no parent")?)?;
            fs::DirBuilder::new()
                .mode(0o700)
                .create(path)
                .map_err(|e| e.to_string())?;
        }
        Err(e) => return Err(e.to_string()),
    }
    Ok(())
}
fn container_name(marker: &str) -> Result<String, String> {
    let name = marker
        .lines()
        .find_map(|line| {
            line.trim()
                .strip_prefix("name=")
                .map(|s| s.trim().trim_matches(['\'', '"']))
        })
        .ok_or("/run/.containerenv has no actual container name")?;
    if !(name == "podman-MiOS-DEV" || name.starts_with("podman-MiOS-DEV-"))
        || !name
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"-_.".contains(&b))
    {
        return Err("build-tmux-desktop requires an actual podman-MiOS-DEV container".into());
    }
    Ok(name.to_owned())
}
fn digest_copy(input: &Path, mut output: Option<&mut File>) -> Result<String, String> {
    if !fs::metadata(input).map_err(|e| e.to_string())?.is_file() {
        return Err(format!("{} must be a regular file", input.display()));
    }
    let mut file = File::open(input).map_err(|e| e.to_string())?;
    let mut hash = Sha256::new();
    let mut buffer = [0u8; 65536];
    loop {
        let n = file.read(&mut buffer).map_err(|e| e.to_string())?;
        if n == 0 {
            break;
        }
        hash.update(&buffer[..n]);
        if let Some(out) = output.as_deref_mut() {
            out.write_all(&buffer[..n]).map_err(|e| e.to_string())?;
        }
    }
    Ok(format!("{:x}", hash.finalize()))
}
fn command(program: &Path, args: &[String], cwd: &Path) -> Result<(), String> {
    let status = Command::new(program)
        .args(args)
        .current_dir(cwd)
        .stdin(Stdio::null())
        .status()
        .map_err(|e| format!("{}: {e}", program.display()))?;
    if status.success() {
        Ok(())
    } else {
        Err(format!("{} failed: {status}", program.display()))
    }
}
fn listing(archive: &Path, verbose: bool, cwd: &Path) -> Result<String, String> {
    let out = Command::new("tar")
        .arg(if verbose { "-tvzf" } else { "-tzf" })
        .arg(archive)
        .current_dir(cwd)
        .stdin(Stdio::null())
        .stderr(Stdio::inherit())
        .output()
        .map_err(|e| e.to_string())?;
    if !out.status.success() {
        return Err("cannot inspect verified source tar".into());
    }
    String::from_utf8(out.stdout).map_err(|e| e.to_string())
}
fn regular_destination(path: &Path) -> Result<(), String> {
    match fs::symlink_metadata(path) {
        Ok(m) if m.is_file() && !m.file_type().is_symlink() => Ok(()),
        Ok(_) => Err(format!(
            "refusing non-regular staging target {}",
            path.display()
        )),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(e) => Err(e.to_string()),
    }
}
fn stage(binary: &Path, target: &Path, mut receipt: serde_json::Value) -> Result<(), String> {
    let parent = target.parent().ok_or("binary destination has no parent")?;
    directories(parent)?;
    regular_destination(target)?;
    let mut name = target
        .file_name()
        .ok_or("binary destination has no file name")?
        .to_os_string();
    name.push(".build.json");
    let receipt_path = parent.join(name);
    regular_destination(&receipt_path)?;
    let mut tmp = tempfile::NamedTempFile::new_in(parent).map_err(|e| e.to_string())?;
    let hash = digest_copy(binary, Some(tmp.as_file_mut()))?;
    tmp.as_file()
        .set_permissions(fs::Permissions::from_mode(0o755))
        .map_err(|e| e.to_string())?;
    tmp.as_file().sync_all().map_err(|e| e.to_string())?;
    receipt["binary_sha256"] = serde_json::json!(hash);
    receipt["binary_path"] = serde_json::json!(target);
    let mut record = tempfile::NamedTempFile::new_in(parent).map_err(|e| e.to_string())?;
    record
        .write_all(&serde_json::to_vec_pretty(&receipt).map_err(|e| e.to_string())?)
        .and_then(|_| record.write_all(b"\n"))
        .map_err(|e| e.to_string())?;
    record.as_file().sync_all().map_err(|e| e.to_string())?;
    tmp.persist(target).map_err(|e| e.to_string())?;
    record.persist(&receipt_path).map_err(|e| e.to_string())?;
    File::open(parent)
        .and_then(|f| f.sync_all())
        .map_err(|e| e.to_string())?;
    println!("{}", receipt_path.display());
    Ok(())
}

/// Host wrapper owns memory admission, the heavy lock and container resources.
pub fn run(
    root: &Path,
    archive: &Path,
    build_dir: &Path,
    install_root: &Path,
) -> Result<(), String> {
    let marker = fs::read_to_string("/run/.containerenv")
        .map_err(|e| format!("run inside the admitted podman-MiOS-DEV container: {e}"))?;
    let container = container_name(&marker)?;
    let data = mios_resolver::resolve_merged(Some(root), false).map_err(|e| e.to_string())?;
    let desktop = data
        .get("keybindings")
        .and_then(|v| v.get("tmux"))
        .and_then(|v| v.get("desktop"))
        .ok_or("missing keybindings.tmux.desktop")?;
    let source = desktop
        .get("source")
        .ok_or("missing keybindings.tmux.desktop.source")?;
    let version = field(source, "version")?;
    if !version
        .bytes()
        .all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b))
    {
        return Err("invalid upstream tmux version".into());
    }
    let url = field(source, "url")?;
    if !url.starts_with("https://") || url.chars().any(char::is_whitespace) {
        return Err("desktop source URL must be HTTPS without whitespace".into());
    }
    let expected = field(source, "sha256")?.to_ascii_lowercase();
    if expected.len() != 64 || !expected.bytes().all(|b| b.is_ascii_hexdigit()) {
        return Err("desktop source sha256 must contain 64 hexadecimal characters".into());
    }
    let patch_rel = Path::new(field(source, "patch")?);
    if !normal_relative(patch_rel) {
        return Err("desktop source patch must be repository-relative without traversal".into());
    }
    let root = root.canonicalize().map_err(|e| e.to_string())?;
    let patch = root.join(patch_rel);
    absolute_directory(patch.parent().ok_or("patch parent")?)?;
    regular_destination(&patch)?;
    let configure = source
        .get("configure_args")
        .and_then(Value::as_array)
        .ok_or("desktop source configure_args must be an array")?
        .iter()
        .map(|v| {
            v.as_str()
                .filter(|v| !v.is_empty() && !v.chars().any(char::is_control))
                .map(str::to_owned)
                .ok_or_else(|| "invalid configure argument".to_owned())
        })
        .collect::<Result<Vec<_>, _>>()?;
    let jobs = source
        .get("make_jobs")
        .and_then(Value::as_integer)
        .filter(|n| (1..=2).contains(n))
        .ok_or("desktop source make_jobs must be 1 or 2")?;
    let binary = Path::new(field(desktop, "binary")?);
    let binary_rel = binary
        .strip_prefix("/")
        .map_err(|_| "desktop binary must be absolute")?;
    if !normal_relative(binary_rel) {
        return Err("desktop binary must not escape the install root".into());
    }
    absolute_directory(install_root)?;
    fresh_build_directory(build_dir)?;

    // Extract the copied, hashed bytes; the original input cannot change between verification and use.
    let mut tar = tempfile::NamedTempFile::new_in(build_dir).map_err(|e| e.to_string())?;
    let actual = digest_copy(archive, Some(tar.as_file_mut()))?;
    if actual != expected {
        return Err(format!(
            "source archive SHA256 mismatch: expected {expected}, got {actual}"
        ));
    }
    tar.as_file().sync_all().map_err(|e| e.to_string())?;
    let mut patch_input = tempfile::NamedTempFile::new_in(build_dir).map_err(|e| e.to_string())?;
    let patch_hash = digest_copy(&patch, Some(patch_input.as_file_mut()))?;
    patch_input
        .as_file()
        .sync_all()
        .map_err(|e| e.to_string())?;
    let top = format!("tmux-{version}");
    let names = listing(tar.path(), false, build_dir)?;
    if names.is_empty()
        || names.lines().any(|name| {
            let path = Path::new(name.trim_end_matches('/'));
            !normal_relative(path)
                || path.components().next() != Some(Component::Normal(top.as_ref()))
        })
    {
        return Err("source tar contains escaped paths or an unexpected top directory".into());
    }
    if listing(tar.path(), true, build_dir)?
        .lines()
        .any(|line| !matches!(line.as_bytes().first(), Some(b'-' | b'd')))
    {
        return Err("source tar may contain only regular files and directories".into());
    }
    command(
        Path::new("tar"),
        &[
            "--extract".into(),
            "--gzip".into(),
            "--no-same-owner".into(),
            "--no-same-permissions".into(),
            "--file".into(),
            tar.path().display().to_string(),
            "--directory".into(),
            build_dir.display().to_string(),
        ],
        build_dir,
    )?;
    let tree = build_dir.join(top);
    for entry in walkdir::WalkDir::new(&tree).follow_links(false) {
        let entry = entry.map_err(|e| e.to_string())?;
        if !entry.file_type().is_dir() && !entry.file_type().is_file() {
            return Err("extracted source contains a link or special file".into());
        }
    }
    command(
        Path::new("patch"),
        &[
            "--batch".into(),
            "--forward".into(),
            "--fuzz=0".into(),
            "-p1".into(),
            "--input".into(),
            patch_input.path().display().to_string(),
        ],
        &tree,
    )?;
    command(&tree.join("configure"), &configure, &tree)?;
    command(Path::new("make"), &[format!("-j{jobs}")], &tree)?;
    let built = tree.join("tmux");
    regular_destination(&built)?;
    command(Path::new("strip"), &[built.display().to_string()], &tree)?;
    stage(
        &built,
        &install_root.join(binary_rel),
        serde_json::json!({
            "schema": 1, "kind": "mios-tmux-desktop-build", "container": container,
            "source_version": version, "source_url": url, "source_sha256": actual,
            "patch": patch_rel, "patch_sha256": patch_hash, "configure_args": configure,
            "make_jobs": jobs, "build_dir": build_dir, "install_root": install_root,
        }),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn actual_container_name_is_required() {
        assert!(container_name("name=\"podman-MiOS-DEV-build\"\n").is_ok());
        assert!(container_name("name=\"podman-MiOS-DEV\"\n").is_ok());
        for bad in [
            "",
            "name=\"other\"",
            "name=\"pretend-podman-MiOS-DEV\"",
            "name=\"podman-MiOS-DEV/escape\"",
        ] {
            assert!(container_name(bad).is_err());
        }
    }
    #[test]
    fn staging_paths_reject_escape_and_filesystem_root() {
        for bad in ["/", "/tmp/../usr", "relative"] {
            assert!(absolute_directory(Path::new(bad)).is_err());
        }
        for bad in ["", "/usr/bin/tmux", "../usr", "usr/../bin"] {
            assert!(!normal_relative(Path::new(bad)));
        }
        assert!(normal_relative(Path::new("usr/libexec/mios/mios-tmux")));
    }
    #[test]
    fn existing_build_tree_must_be_private_and_empty() {
        let tmp = tempfile::tempdir().unwrap();
        fs::set_permissions(tmp.path(), fs::Permissions::from_mode(0o700)).unwrap();
        let checked = fresh_build_directory(tmp.path());
        assert!(
            checked.is_ok(),
            "private build fixture {}: {checked:?}",
            tmp.path().display()
        );
        fs::write(tmp.path().join("old"), b"old").unwrap();
        assert!(fresh_build_directory(tmp.path()).is_err());
        fs::remove_file(tmp.path().join("old")).unwrap();
        fs::set_permissions(tmp.path(), fs::Permissions::from_mode(0o755)).unwrap();
        assert!(fresh_build_directory(tmp.path()).is_err());
        let alias = tmp.path().join("alias");
        std::os::unix::fs::symlink(tmp.path(), &alias).unwrap();
        assert!(fresh_build_directory(&alias).is_err());
    }
}
