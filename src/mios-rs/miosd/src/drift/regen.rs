// AI-hint: Helper functions for running generator scripts and diffing against committed SSOT targets in clean environments.
// AI-related: automation/98-drift-checks.sh, src/mios-rs/miosd/src/drift/mod.rs

use super::{DriftCtx, Verdict};
use std::collections::HashMap;
use std::env;
use std::fs;
use std::path::Path;
use std::process::Command;

pub fn regen_and_diff(
    ctx: &DriftCtx,
    gen_relpath: &str,
    targets: &[&str],
    extra_args: &[&str],
) -> Verdict {
    // T-1045. This function is named regen_and_diff and it does not diff: it
    // runs the generator and then asserts the target EXISTS. That is only sound
    // when the generator itself compares, i.e. when --check is passed and its
    // exit status is the verdict. Called without it the generator WRITES --
    // erasing the very drift the check is looking for, and mutating the tree
    // from inside a read-only gate. Refuse rather than mislead.
    if !extra_args.contains(&"--check") {
        return Verdict::Fail(format!(
            "regen_and_diff({gen_relpath}) called without --check: it would regenerate the \
             target and then only confirm the file exists, which cannot detect drift. Pass \
             --check, or use a helper that snapshots and compares."
        ));
    }
    let gen_path = ctx.root.join(gen_relpath);
    if !gen_path.exists() {
        return Verdict::Skip(format!(
            "Generator script not found: {}",
            gen_path.display()
        ));
    }

    let python_cmd = if Command::new("python3").arg("--version").output().is_ok() {
        "python3"
    } else if Command::new("python").arg("--version").output().is_ok() {
        "python"
    } else {
        return Verdict::Skip("Python interpreter not found".to_string());
    };

    // Clean env isolation to prevent env leakage
    let mut clean_env = HashMap::new();
    if let Ok(path) = env::var("PATH") {
        clean_env.insert("PATH", path);
    }
    if let Ok(home) = env::var("HOME") {
        clean_env.insert("HOME", home);
    }
    clean_env.insert("MIOS_ROOT", ctx.root.to_string_lossy().to_string());

    let mut cmd = Command::new(python_cmd);
    cmd.arg(&gen_path);
    cmd.args(extra_args);

    cmd.env_clear();
    for (k, v) in &clean_env {
        cmd.env(k, v);
    }

    let output = match cmd.output() {
        Ok(out) => out,
        Err(e) => {
            return Verdict::Fail(format!(
                "Failed to execute generator {}: {}",
                gen_relpath, e
            ))
        }
    };

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Verdict::Fail(format!(
            "Generator {} exited with error: {}",
            gen_relpath,
            stderr.trim()
        ));
    }

    for target in targets {
        let target_path = ctx.root.join(target);
        if !target_path.exists() {
            return Verdict::Fail(format!("Target artifact missing: {}", target));
        }
    }

    Verdict::Pass(format!("Projection check {} passed cleanly", gen_relpath))
}

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
// was strangler-deleted (AGY-1073) and its miosd caller moved to
// regen_and_compare_native below. Every surviving drift regen check invokes a
// native binary.

/// Resolve a tools/native generator binary the way the bash gates do
/// (_gate_bin / native_bin): release before debug, .exe before extensionless,
/// /usr/libexec fallback. Windows finds the .exe first; the extensionless
/// Linux ELF is never executable there, so ordering carries the platform.
pub fn resolve_native_generator(ctx: &DriftCtx, name: &str) -> Option<std::path::PathBuf> {
    let mut candidates: Vec<std::path::PathBuf> = Vec::new();
    for dir in ["tools/native/target/release", "tools/native/target/debug"] {
        candidates.push(ctx.root.join(dir).join(format!("{name}.exe")));
        candidates.push(ctx.root.join(dir).join(name));
    }
    candidates.push(std::path::PathBuf::from("/usr/libexec/mios").join(name));
    candidates.into_iter().find(|p| p.is_file())
}

/// Native-generator twin of regen_and_compare_file. The Python generator was
/// strangler-deleted (AGY-1073) while this caller still named the .py path and
/// the python3 interpreter, failing image builds with "Generator not found".
/// Snapshots EVERY artifact the generator may rewrite (the python twin
/// restored only one of the two this generator writes), runs the binary
/// directly, restores everything before judging, and never returns a Pass it
/// did not compare.
pub fn regen_and_compare_native(ctx: &DriftCtx, bin_name: &str, targets: &[&str]) -> Verdict {
    let Some(bin) = resolve_native_generator(ctx, bin_name) else {
        return Verdict::Fail(format!(
            "native generator not built: {bin_name} -- build it: cd tools/native && cargo build -p {bin_name}"
        ));
    };

    // Snapshot every target before running anything.
    let mut snapshots: Vec<(&str, std::path::PathBuf, Vec<u8>)> = Vec::new();
    for t in targets {
        let p = ctx.root.join(t);
        match fs::read(&p) {
            Ok(b) => snapshots.push((t, p, b)),
            Err(e) => {
                return Verdict::Fail(format!("Target artifact {t} unreadable: {e}"));
            }
        }
    }

    let mut cmd = Command::new(&bin);
    cmd.env_clear();
    if let Ok(p) = env::var("PATH") {
        cmd.env("PATH", p);
    }
    if let Ok(h) = env::var("HOME") {
        cmd.env("HOME", h);
    }
    cmd.env("MIOS_ROOT", ctx.root.as_os_str())
        .env("MIOS_DRIFT_ROOT", ctx.root.as_os_str());
    let out = match cmd.output() {
        Ok(o) => o,
        Err(e) => {
            // Restore before returning, so even an exec failure leaves no
            // half-run state -- nothing was written yet, but the contract is
            // uniform.
            return Verdict::Fail(format!("Failed to execute {}: {e}", bin.display()));
        }
    };

    // Read the post-run bytes, THEN restore every snapshot, THEN judge: the
    // tree is left exactly as found whatever the verdict says.
    let after: Vec<Vec<u8>> = snapshots
        .iter()
        .map(|(_, p, _)| fs::read(p).unwrap_or_default())
        .collect();
    for (_, p, before) in &snapshots {
        let _ = fs::write(p, before);
    }

    if !out.status.success() {
        return Verdict::Fail(format!(
            "{} exited with error: {}",
            bin.display(),
            String::from_utf8_lossy(&out.stderr).trim()
        ));
    }
    let mut stale: Vec<String> = Vec::new();
    for ((t, _, before), now) in snapshots.iter().zip(after.iter()) {
        if now != before {
            stale.push(format!(
                "{t}: committed {} bytes, generator renders {}",
                before.len(),
                now.len()
            ));
        }
    }
    if !stale.is_empty() {
        return Verdict::Fail(format!(
            "stale projection(s) -- run {} via tools/sync-generated.sh: {}",
            bin.display(),
            stale.join("; ")
        ));
    }
    let total: usize = snapshots.iter().map(|(_, _, b)| b.len()).sum();
    Verdict::Pass(format!(
        "{} projection(s) match what {} renders ({} bytes total)",
        snapshots.len(),
        bin.display(),
        total
    ))
}

#[cfg(test)]
mod tests {
    // Test code: unwrap() is the intended failure mode -- a panic here IS the
    // assertion. Scoped to this module so production unwraps stay lint errors.
    #![allow(clippy::unwrap_used)]

    use super::*;
    use tempfile::TempDir;

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
