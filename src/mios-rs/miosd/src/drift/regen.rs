// AI-hint: Helper functions for running generator scripts and diffing against committed SSOT targets in clean environments.
// AI-related: automation/98-drift-checks.sh, src/mios-rs/miosd/src/drift/mod.rs

use super::{DriftCtx, Verdict};
use std::env;
use std::fs;
use std::path::Path;
use std::process::Command;

pub fn regen_and_diff_shell(
    ctx: &DriftCtx,
    script_relpath: &str,
    out_env: &str,
    target: &str,
) -> Verdict {
    let script = ctx.root.join(script_relpath);
    if !script.exists() {
        // Loud, not Skip: a registered check whose renderer is absent is a
        // registry bug, not an environment quirk.
        return Verdict::Fail(format!(
            "Renderer not found: {} (registered by a drift check)",
            script_relpath
        ));
    }
    let committed = ctx.root.join(target);
    if !committed.exists() {
        return Verdict::Fail(format!("Target artifact missing: {}", target));
    }

    let scratch = std::env::temp_dir().join(format!("mios-drift-{}", std::process::id()));
    let _ = fs::remove_dir_all(&scratch);
    if let Err(e) = fs::create_dir_all(&scratch) {
        return Verdict::Fail(format!("Cannot create scratch dir: {e}"));
    }

    // Seed the scratch with the committed artifact so a renderer that edits in
    // place has the same starting point the real one does.
    let out_path = if committed.is_dir() {
        if let Err(e) = copy_dir(&committed, &scratch) {
            return Verdict::Fail(format!("Cannot seed scratch from {target}: {e}"));
        }
        scratch.clone()
    } else {
        let dst = scratch.join(committed.file_name().unwrap_or_default());
        if let Err(e) = fs::copy(&committed, &dst) {
            return Verdict::Fail(format!("Cannot seed scratch from {target}: {e}"));
        }
        dst
    };

    let toml = ctx.root.join("usr/share/mios/mios.toml");
    let mut cmd = Command::new("bash");
    cmd.arg(&script);
    cmd.env_clear();
    if let Ok(p) = env::var("PATH") {
        cmd.env("PATH", p);
    }
    cmd.env("MIOS_TOML", &toml)
        .env("MIOS_VENDOR_TOML", &toml)
        .env("MIOS_ROOT", &ctx.root)
        .env(out_env, &out_path);

    match cmd.output() {
        Err(e) => return Verdict::Fail(format!("Failed to run {script_relpath}: {e}")),
        Ok(out) if !out.status.success() => {
            return Verdict::Fail(format!(
                "Renderer {script_relpath} failed: {}",
                String::from_utf8_lossy(&out.stderr).trim()
            ))
        }
        Ok(_) => {}
    }

    let drifted = diff_tree(&committed, &out_path);
    let _ = fs::remove_dir_all(&scratch);

    if drifted.is_empty() {
        Verdict::Pass(format!("{target} matches the SSOT projection"))
    } else {
        Verdict::Fail(format!(
            "{target} drifted from mios.toml -- re-run {script_relpath}: {}",
            drifted.join(", ")
        ))
    }
}

fn copy_dir(src: &Path, dst: &Path) -> std::io::Result<()> {
    fs::create_dir_all(dst)?;
    for entry in fs::read_dir(src)? {
        let entry = entry?;
        let to = dst.join(entry.file_name());
        if entry.file_type()?.is_dir() {
            copy_dir(&entry.path(), &to)?;
        } else {
            fs::copy(entry.path(), to)?;
        }
    }
    Ok(())
}

/// Names that differ in content. Line endings are normalised: a Windows checkout
/// and the Linux build must not read as drift.
fn diff_tree(committed: &Path, rendered: &Path) -> Vec<String> {
    fn norm(p: &Path) -> Option<String> {
        fs::read_to_string(p)
            .ok()
            .map(|s| s.replace("\r\n", "\n").trim_end().to_string())
    }
    let mut out = Vec::new();
    if committed.is_file() {
        if norm(committed) != norm(rendered) {
            out.push(
                committed
                    .file_name()
                    .map(|s| s.to_string_lossy().into_owned())
                    .unwrap_or_default(),
            );
        }
        return out;
    }
    let entries = match fs::read_dir(committed) {
        Ok(e) => e,
        Err(_) => return vec!["<unreadable>".to_string()],
    };
    for entry in entries.flatten() {
        if !entry.path().is_file() {
            continue;
        }
        let name = entry.file_name();
        let there = rendered.join(&name);
        if !there.exists() || norm(&entry.path()) != norm(&there) {
            out.push(name.to_string_lossy().into_owned());
        }
    }
    out
}

// regen_and_compare_file (python3-interpreted regen for .py generators) was
// deleted with the last of its consumers: the names-registry Python generator
// was strangler-deleted (task_1713) and its miosd caller moved to
// regen_and_compare_native below. Every surviving drift regen check invokes a
// native binary.

/// Select an executable for the running platform, never a Windows PE on Linux.
pub fn resolve_native_generator(ctx: &DriftCtx, name: &str) -> Option<std::path::PathBuf> {
    let executable = format!("{name}{}", std::env::consts::EXE_SUFFIX);
    if let Some(directory) = env::var_os("MIOS_NATIVE_BIN_DIR") {
        let path = std::path::PathBuf::from(directory).join(&executable);
        return path.is_file().then_some(path);
    }
    let mut candidates = ["tools/native/target/release", "tools/native/target/debug"]
        .map(|dir| ctx.root.join(dir).join(&executable))
        .to_vec();
    if cfg!(unix) {
        candidates.extend(
            ["/usr/bin", "/usr/libexec/mios"]
                .map(|dir| std::path::Path::new(dir).join(&executable)),
        );
    }
    candidates.into_iter().find(|p| p.is_file())
}

/// Compare all projections and restore all snapshots even when generation fails.
pub fn regen_and_compare_native(ctx: &DriftCtx, bin_name: &str, targets: &[&str]) -> Verdict {
    let Some(bin) = resolve_native_generator(ctx, bin_name) else {
        return Verdict::Fail(format!("native generator not built: {bin_name} -- build it: cd tools/native && cargo build -p {bin_name}"));
    };
    compare_native_run(ctx, &bin.display().to_string(), targets, || {
        let mut cmd = Command::new(&bin);
        cmd.env_clear().current_dir(&ctx.root);
        for key in ["PATH", "HOME", "USERPROFILE", "SystemRoot", "TEMP", "TMP"] {
            if let Some(value) = env::var_os(key) {
                cmd.env(key, value);
            }
        }
        cmd.env("MIOS_ROOT", &ctx.root)
            .env("MIOS_DRIFT_ROOT", &ctx.root);
        let output = cmd
            .output()
            .map_err(|e| format!("execute {}: {e}", bin.display()))?;
        if !output.status.success() {
            return Err(format!(
                "{} exited with error {}: {}",
                bin.display(),
                output.status,
                String::from_utf8_lossy(&output.stderr).trim()
            ));
        }
        Ok(())
    })
}

fn compare_native_run(
    ctx: &DriftCtx,
    label: &str,
    targets: &[&str],
    generate: impl FnOnce() -> Result<(), String>,
) -> Verdict {
    if targets.is_empty() {
        return Verdict::Fail("native projection target set is empty".into());
    }
    let mut snapshots = Vec::new();
    let mut seen = std::collections::BTreeSet::new();
    for target in targets {
        let relative = std::path::Path::new(target);
        if relative.is_absolute()
            || !seen.insert(target)
            || relative
                .components()
                .any(|c| !matches!(c, std::path::Component::Normal(_)))
        {
            return Verdict::Fail(format!("invalid or duplicate projection target {target}"));
        }
        let path = ctx.root.join(target);
        let metadata = match fs::symlink_metadata(&path) {
            Ok(m) if m.file_type().is_file() => m,
            Ok(_) => {
                return Verdict::Fail(format!("Target artifact {target} is not a regular file"))
            }
            Err(e) => return Verdict::Fail(format!("Target artifact {target} unreadable: {e}")),
        };
        let bytes = match fs::read(&path) {
            Ok(bytes) => bytes,
            Err(e) => return Verdict::Fail(format!("Target artifact {target} unreadable: {e}")),
        };
        snapshots.push((*target, path, bytes, metadata.permissions()));
    }
    let generated = generate();
    let after = snapshots
        .iter()
        .map(|(_, p, _, _)| fs::read(p))
        .collect::<Vec<_>>();
    let mut failures = Vec::new();
    // Attempt every restore; one failure must not prevent the others.
    for (target, path, bytes, permissions) in &snapshots {
        if let Err(e) =
            fs::write(path, bytes).and_then(|()| fs::set_permissions(path, permissions.clone()))
        {
            failures.push(format!("restore {target} failed: {e}"));
        }
    }
    if let Err(e) = generated {
        failures.push(e);
    }
    for ((target, _, before, _), result) in snapshots.iter().zip(after) {
        match result {
            Err(e) => failures.push(format!("read regenerated {target} failed: {e}")),
            Ok(now) if &now != before => failures.push(format!(
                "{target}: committed {} bytes, generator renders {}",
                before.len(),
                now.len()
            )),
            Ok(_) => {}
        }
    }
    if !failures.is_empty() {
        return Verdict::Fail(format!("{label} projection verification failed: {}; run tools/sync-generated.sh for stale projections", failures.join("; ")));
    }
    Verdict::Pass(format!(
        "{} projection(s) match what {label} renders ({} bytes total)",
        snapshots.len(),
        snapshots.iter().map(|(_, _, b, _)| b.len()).sum::<usize>()
    ))
}

#[cfg(test)]
mod tests {
    // Test code: unwrap() is the intended failure mode -- a panic here IS the
    // assertion. Scoped to this module so production unwraps stay lint errors.
    #![allow(clippy::unwrap_used)]

    use super::*;
    use tempfile::TempDir;

    fn native_fixture() -> (TempDir, DriftCtx) {
        let root = TempDir::new().unwrap();
        let ctx = DriftCtx {
            root: root.path().into(),
            soft: false,
            in_image: false,
            git_ok: true,
            incomplete_tree: false,
        };
        fs::write(root.path().join("a"), b"original").unwrap();
        fs::write(root.path().join("b"), b"second").unwrap();
        (root, ctx)
    }

    #[test]
    fn native_comparison_clean_and_stale_controls_restore_all_targets() {
        let (root, ctx) = native_fixture();
        assert!(matches!(
            compare_native_run(&ctx, "fixture", &["a", "b"], || Ok(())),
            Verdict::Pass(_)
        ));
        let verdict = compare_native_run(&ctx, "fixture", &["a", "b"], || {
            fs::write(root.path().join("a"), b"changed").unwrap();
            fs::write(root.path().join("b"), b"also changed").unwrap();
            Ok(())
        });
        assert!(
            matches!(verdict, Verdict::Fail(ref text) if text.contains("a: committed") && text.contains("b: committed"))
        );
        assert_eq!(fs::read(root.path().join("a")).unwrap(), b"original");
        assert_eq!(fs::read(root.path().join("b")).unwrap(), b"second");
    }

    #[test]
    fn native_generator_errors_restore_and_empty_target_sets_fail() {
        let (root, ctx) = native_fixture();
        let verdict = compare_native_run(&ctx, "fixture", &["a", "b"], || {
            fs::write(root.path().join("a"), b"partial output").unwrap();
            Err("planted generator failure".into())
        });
        assert!(
            matches!(verdict, Verdict::Fail(ref text) if text.contains("planted generator failure"))
        );
        assert_eq!(fs::read(root.path().join("a")).unwrap(), b"original");
        let ran = std::cell::Cell::new(false);
        assert!(matches!(
            compare_native_run(&ctx, "fixture", &[], || {
                ran.set(true);
                Ok(())
            }),
            Verdict::Fail(_)
        ));
        assert!(!ran.get());
    }

    #[test]
    fn native_read_error_cannot_match_an_empty_original() {
        let (root, ctx) = native_fixture();
        fs::write(root.path().join("a"), b"").unwrap();
        let verdict = compare_native_run(&ctx, "fixture", &["a"], || {
            fs::remove_file(root.path().join("a")).unwrap();
            Ok(())
        });
        assert!(
            matches!(verdict, Verdict::Fail(ref text) if text.contains("read regenerated a failed"))
        );
        assert_eq!(fs::read(root.path().join("a")).unwrap(), b"");
    }

    #[test]
    fn native_restore_failure_is_reported_and_other_restores_are_attempted() {
        let (root, ctx) = native_fixture();
        let verdict = compare_native_run(&ctx, "fixture", &["a", "b"], || {
            fs::remove_file(root.path().join("a")).unwrap();
            fs::create_dir(root.path().join("a")).unwrap();
            fs::write(root.path().join("b"), b"modified").unwrap();
            Ok(())
        });
        assert!(matches!(verdict, Verdict::Fail(ref text) if text.contains("restore a failed")));
        assert_eq!(fs::read(root.path().join("b")).unwrap(), b"second");
    }

    #[test]
    fn test_diff_tree_single_file_identical() {
        let temp = TempDir::new().unwrap();
        let file_a = temp.path().join("a.txt");
        let file_b = temp.path().join("b.txt");
        fs::write(&file_a, "line 1\r\nline 2\n").unwrap();
        fs::write(&file_b, "line 1\nline 2\n").unwrap();

        let diffs = diff_tree(&file_a, &file_b);
        assert!(diffs.is_empty(), "CRLF normalized diff should be empty");
    }

    #[test]
    fn test_diff_tree_single_file_drifted() {
        let temp = TempDir::new().unwrap();
        let file_a = temp.path().join("a.txt");
        let file_b = temp.path().join("b.txt");
        fs::write(&file_a, "line 1\nline 2\n").unwrap();
        fs::write(&file_b, "line 1\nline DIFFERENT\n").unwrap();

        let diffs = diff_tree(&file_a, &file_b);
        assert_eq!(diffs, vec!["a.txt"]);
    }

    #[test]
    fn test_diff_tree_directory_comparison() {
        let temp = TempDir::new().unwrap();
        let dir_a = temp.path().join("dir_a");
        let dir_b = temp.path().join("dir_b");
        fs::create_dir_all(&dir_a).unwrap();
        fs::create_dir_all(&dir_b).unwrap();

        fs::write(dir_a.join("unit.service"), "ExecStart=/bin/true").unwrap();
        fs::write(dir_b.join("unit.service"), "ExecStart=/bin/true").unwrap();
        fs::write(dir_a.join("other.conf"), "a=1").unwrap();
        fs::write(dir_b.join("other.conf"), "a=2").unwrap();

        let diffs = diff_tree(&dir_a, &dir_b);
        assert_eq!(diffs, vec!["other.conf"]);
    }
}
