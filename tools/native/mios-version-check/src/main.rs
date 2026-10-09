// AI-hint: Rust native tool for version SSOT verification (WS-LANG / ADR-0011 / task_1078).
// Reads VERSION, mios.toml [meta].mios_version, and Containerfile ARG MIOS_VERSION to verify parity.

use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::exit;

fn find_repo_root() -> PathBuf {
    if let Ok(root) = env::var("MIOS_DRIFT_ROOT") {
        if !root.is_empty() {
            return PathBuf::from(root);
        }
    }

    let mut dir = env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    loop {
        if dir.join("VERSION").exists() && dir.join("usr/share/mios/mios.toml").exists() {
            return dir;
        }
        if !dir.pop() {
            break;
        }
    }
    PathBuf::from(".")
}

fn extract_toml_version(path: &Path) -> Option<String> {
    let content = fs::read_to_string(path).ok()?;
    let mut in_meta = false;
    for line in content.lines() {
        let trimmed = line.trim();
        if trimmed.starts_with('[') && trimmed.ends_with(']') {
            in_meta = trimmed == "[meta]" || trimmed == "[ai]";
            continue;
        }
        if in_meta && (trimmed.starts_with("mios_version") || trimmed.starts_with("version")) {
            if let Some(pos) = trimmed.find('=') {
                let val = trimmed[pos + 1..]
                    .trim()
                    .trim_matches('"')
                    .trim_matches('\'');
                return Some(val.to_string());
            }
        }
    }
    None
}

fn extract_containerfile_version(path: &Path) -> Option<String> {
    let content = fs::read_to_string(path).ok()?;
    for line in content.lines() {
        let trimmed = line.trim();
        if trimmed.starts_with("ARG MIOS_VERSION=") {
            let val = trimmed
                .trim_start_matches("ARG MIOS_VERSION=")
                .trim()
                .trim_matches('"');
            return Some(val.to_string());
        }
    }
    None
}

fn main() {
    let root = find_repo_root();
    let version_file = root.join("VERSION");
    let toml_file = root.join("usr/share/mios/mios.toml");
    let containerfile = root.join("Containerfile");

    let version_val = match fs::read_to_string(&version_file) {
        Ok(v) => v.trim().to_string(),
        Err(e) => {
            eprintln!(
                "[mios-version-check] ERROR: failed to read VERSION file {:?}: {}",
                version_file, e
            );
            exit(1);
        }
    };

    let toml_val = match extract_toml_version(&toml_file) {
        Some(v) => v,
        None => {
            eprintln!(
                "[mios-version-check] ERROR: failed to find mios_version in {:?}",
                toml_file
            );
            exit(1);
        }
    };

    let cf_val = match extract_containerfile_version(&containerfile) {
        Some(v) => v,
        None => {
            eprintln!(
                "[mios-version-check] WARNING: ARG MIOS_VERSION missing in {:?}",
                containerfile
            );
            version_val.clone()
        }
    };

    if version_val != toml_val || version_val != cf_val {
        eprintln!(
            "[mios-version-check] VIOLATION: Version SSOT mismatch! VERSION={}, mios.toml={}, Containerfile={}",
            version_val, toml_val, cf_val
        );
        exit(1);
    }

    println!(
        "[mios-version-check] PASS: version SSOT verified ({})",
        version_val
    );
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_extract_toml_version() {
        let dir = std::env::temp_dir().join("mios_version_test_toml");
        let _ = fs::create_dir_all(&dir);
        let file_path = dir.join("test.toml");
        fs::write(&file_path, "[meta]\nmios_version = \"1.2.3\"\n").unwrap();
        assert_eq!(extract_toml_version(&file_path), Some("1.2.3".to_string()));
        let _ = fs::remove_file(file_path);
        let _ = fs::remove_dir(dir);
    }

    #[test]
    fn test_extract_containerfile_version() {
        let dir = std::env::temp_dir().join("mios_version_test_cf");
        let _ = fs::create_dir_all(&dir);
        let file_path = dir.join("Containerfile");
        fs::write(&file_path, "ARG MIOS_VERSION=\"1.2.3\"\n").unwrap();
        assert_eq!(
            extract_containerfile_version(&file_path),
            Some("1.2.3".to_string())
        );
        let _ = fs::remove_file(file_path);
        let _ = fs::remove_dir(dir);
    }
}
