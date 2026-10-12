// AI-hint: SSOT lint equivalence checks using native mios-ssot-lint.
// AI-related: tools/native/mios-ssot-lint, automation/97-ssot-lint.sh

use super::{Check, DriftCtx, Verdict};
use std::ffi::OsString;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, ExitStatus, Stdio};

pub struct SSOTLintEquivalenceCheck;
impl Check for SSOTLintEquivalenceCheck {
    fn id(&self) -> &'static str {
        "check_ssot_lint_equivalence"
    }
    fn describe(&self) -> &'static str {
        "Assert native mios-ssot-lint output matches legacy 97-ssot-lint.sh"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        super::audit::verdict(
            native_lint(ctx, std::env::var_os("MIOS_SSOT_LINT_BIN"))
                .and_then(|native| equivalence(ctx, &native)),
        )
    }
}

const LEGACY_LINT: &str = "automation/97-ssot-lint.sh";

/// Same resolution as `audit::native`: the `MIOS_SSOT_LINT_BIN` override alone
/// when set, else the release/install locations. Absent is a Fail: the legacy
/// skip-unless-CI is how the twin could go unbuilt and still read as equivalent.
fn native_lint(ctx: &DriftCtx, override_path: Option<OsString>) -> Result<PathBuf, String> {
    let filename = format!("mios-ssot-lint{}", std::env::consts::EXE_SUFFIX);
    let mut candidates = Vec::<PathBuf>::new();
    if let Some(path) = override_path {
        candidates.push(path.into());
    } else {
        for directory in [
            "tools/native/target/release",
            "src/mios-rs/target/release",
            "usr/bin",
            "usr/libexec/mios",
        ] {
            candidates.push(ctx.root.join(directory).join(&filename));
        }
        if let Some(parent) = std::env::current_exe()
            .ok()
            .as_deref()
            .and_then(Path::parent)
        {
            candidates.push(parent.join(&filename));
        }
        candidates.push(Path::new("/usr/bin").join(&filename));
        candidates.push(Path::new("/usr/libexec/mios").join(&filename));
    }
    candidates.into_iter().find(|path| path.is_file()).ok_or_else(|| {
        format!("required native mios-ssot-lint unavailable (set MIOS_SSOT_LINT_BIN or install the SSOT release catalog), so its equivalence to {LEGACY_LINT} is unverified")
    })
}

/// Both lints run against the same root, stdout and stderr merged as `2>&1`
/// does, and must agree on exit code and on output once root spellings are
/// normalised. Agreement is the property: a lint that fails identically in both
/// languages is still equivalent (97-ssot-lint is gated on its own).
fn equivalence(ctx: &DriftCtx, native: &Path) -> super::audit::Audit {
    let script = ctx.root.join(LEGACY_LINT);
    if !script.is_file() {
        return Err(format!(
            "{LEGACY_LINT} is missing, so mios-ssot-lint has nothing to be equivalent to"
        ));
    }
    let mut legacy = Command::new("bash");
    legacy.arg(&script);
    let (bash_status, bash_output) =
        merged(legacy, &ctx.root).map_err(|e| format!("bash {LEGACY_LINT}: {e}"))?;
    let (rust_status, rust_output) = merged(Command::new(native), &ctx.root)
        .map_err(|e| format!("{}: {e}", native.display()))?;
    let (bash_code, rust_code) = match (bash_status.code(), rust_status.code()) {
        (Some(bash), Some(rust)) => (bash, rust),
        _ => return Err(format!("a lint was killed by a signal (bash {LEGACY_LINT} {bash_status}, mios-ssot-lint {rust_status})")),
    };
    if bash_code != rust_code {
        return Err(format!("mios-ssot-lint exit code ({rust_code}) differs from bash {LEGACY_LINT} ({bash_code})\n--- bash\n{bash_output}\n--- mios-ssot-lint\n{rust_output}"));
    }
    let (bash_lines, rust_lines) = (
        normalize(&bash_output, &ctx.root),
        normalize(&rust_output, &ctx.root),
    );
    if bash_lines != rust_lines {
        let (bash, rust): (Vec<_>, Vec<_>) =
            (bash_lines.lines().collect(), rust_lines.lines().collect());
        let line = bash
            .iter()
            .zip(&rust)
            .position(|(a, b)| a != b)
            .unwrap_or(bash.len().min(rust.len()));
        return Err(format!(
            "mios-ssot-lint output differs from bash {LEGACY_LINT} (exit codes both {bash_code}); first difference at output line {}:\n  bash: {}\n  rust: {}",
            line + 1,
            bash.get(line).unwrap_or(&"<end of output>"),
            rust.get(line).unwrap_or(&"<end of output>")
        ));
    }
    Ok(format!(
        "mios-ssot-lint ({}) byte-identical to bash {LEGACY_LINT} after root normalisation: exit {bash_code}, {} line(s)",
        native.display(),
        bash_lines.lines().count()
    ))
}

/// Run with stdout and stderr on ONE pipe, so interleaving is preserved exactly
/// as a shell `2>&1` would. The soft-mode switch is removed so both are compared
/// on their strict path.
fn merged(mut command: Command, root: &Path) -> Result<(ExitStatus, String), String> {
    let (mut reader, writer) = std::io::pipe().map_err(|e| format!("pipe: {e}"))?;
    let error_writer = writer.try_clone().map_err(|e| format!("pipe: {e}"))?;
    let mut child = command
        .current_dir(root)
        .env("MIOS_SSOT_LINT_ROOT", root)
        .env_remove("MIOS_SSOT_LINT_SOFT")
        .stdin(Stdio::null())
        .stdout(writer)
        .stderr(error_writer)
        .spawn()
        .map_err(|e| e.to_string())?;
    // The Command still owns the parent's write ends; until it is dropped the
    // pipe never reaches EOF.
    drop(command);
    let mut output = Vec::new();
    reader.read_to_end(&mut output).map_err(|e| e.to_string())?;
    let status = child.wait().map_err(|e| e.to_string())?;
    Ok((status, String::from_utf8_lossy(&output).into_owned()))
}

/// The legacy sed mapped hard-coded `/mnt/c/MiOS`, `C:\MiOS` and `c:\MiOS` to
/// /ROOT. Here every spelling of the actual root is derived: as given, its
/// canonical form, the WSL `/mnt/<d>/` and Windows `<D>:/` views of each, and
/// either drive-letter case. Backslashes become `/` first, then longest
/// spelling wins. Trailing newlines are dropped as `$(...)` dropped them.
fn normalize(text: &str, root: &Path) -> String {
    let mut seeds = vec![root.to_string_lossy().into_owned()];
    if let Ok(canonical) = root.canonicalize() {
        seeds.push(canonical.to_string_lossy().into_owned());
    }
    let mut spellings = Vec::new();
    for seed in seeds {
        let slashed = seed.replace('\\', "/");
        let slashed = slashed
            .strip_prefix("//?/")
            .unwrap_or(&slashed)
            .trim_end_matches('/')
            .to_owned();
        let bytes = slashed.as_bytes();
        let mut views = vec![slashed.clone()];
        if bytes.len() >= 2 && bytes[0].is_ascii_alphabetic() && bytes[1] == b':' {
            views.push(format!(
                "/mnt/{}{}",
                (bytes[0] as char).to_ascii_lowercase(),
                &slashed[2..]
            ));
        } else if let Some(rest) = slashed.strip_prefix("/mnt/") {
            let mut chars = rest.chars();
            if let (Some(drive), tail) = (chars.next(), chars.as_str()) {
                if drive.is_ascii_alphabetic() && (tail.is_empty() || tail.starts_with('/')) {
                    views.push(format!("{}:{tail}", drive.to_ascii_uppercase()));
                }
            }
        }
        for view in views {
            let view_bytes = view.as_bytes();
            if view_bytes.len() >= 2 && view_bytes[0].is_ascii_alphabetic() && view_bytes[1] == b':'
            {
                spellings.push(format!(
                    "{}{}",
                    (view_bytes[0] as char).to_ascii_lowercase(),
                    &view[1..]
                ));
                spellings.push(format!(
                    "{}{}",
                    (view_bytes[0] as char).to_ascii_uppercase(),
                    &view[1..]
                ));
            } else {
                spellings.push(view);
            }
        }
    }
    // An empty or bare "/" root has no spelling to normalise: replacing every
    // "/" would mangle both outputs alike and prove nothing.
    spellings.retain(|spelling| !spelling.is_empty());
    spellings.sort_by_key(|spelling| std::cmp::Reverse(spelling.len()));
    spellings.dedup();
    let mut normalized = text.replace('\\', "/");
    for spelling in spellings {
        normalized = normalized.replace(&spelling, "/ROOT");
    }
    normalized.trim_end_matches(['\n', '\r']).to_owned()
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use std::fs;
    use std::os::unix::fs::PermissionsExt;

    const LEGACY: &str = "echo \"[97-ssot-lint] userenv: $MIOS_SSOT_LINT_ROOT/tools/lib/userenv.sh\"\necho '[97-ssot-lint] ERROR: dead key' >&2\necho '[97-ssot-lint] checked 1'\nexit 1\n";

    fn native(dir: &Path, body: &str) -> Result<PathBuf, Box<dyn std::error::Error>> {
        let path = dir.join("mios-ssot-lint");
        fs::write(&path, format!("#!/bin/sh\n{body}"))?;
        fs::set_permissions(&path, fs::Permissions::from_mode(0o755))?;
        Ok(path)
    }

    #[test]
    fn twins_must_agree_on_exit_code_and_merged_output_after_root_normalisation(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let temp = tempfile::tempdir()?;
        let tools = tempfile::tempdir()?;
        let root = temp.path();
        let ctx = DriftCtx::new(root.into(), false);
        let missing = tools.path().join("absent-mios-ssot-lint");
        assert!(native_lint(&ctx, Some(missing.clone().into()))
            .is_err_and(|e| e.contains("required native mios-ssot-lint unavailable")));
        let twin = native(tools.path(), LEGACY)?;
        assert_eq!(native_lint(&ctx, Some(twin.clone().into()))?, twin);
        assert!(equivalence(&ctx, &twin)
            .is_err_and(|e| e.contains("automation/97-ssot-lint.sh is missing")));
        fs::create_dir_all(root.join("automation"))?;
        fs::write(root.join(LEGACY_LINT), LEGACY)?;
        let pass = equivalence(&ctx, &twin)?;
        assert!(
            pass.contains("byte-identical") && pass.contains("exit 1, 3 line(s)"),
            "{pass}"
        );
        // The same root printed in Windows spelling still matches.
        let windows = native(
            tools.path(),
            &LEGACY.replace(
                "echo \"[97-ssot-lint] userenv: $MIOS_SSOT_LINT_ROOT/tools/lib/userenv.sh\"",
                "w=$(printf %s \"$MIOS_SSOT_LINT_ROOT\" | tr / '\\\\')\nprintf '%s\\n' \"[97-ssot-lint] userenv: $w\\\\tools\\\\lib\\\\userenv.sh\"",
            ),
        )?;
        assert!(
            equivalence(&ctx, &windows).is_ok(),
            "{:?}",
            equivalence(&ctx, &windows)
        );
        let code = native(tools.path(), &LEGACY.replace("exit 1", "exit 0"))?;
        assert!(equivalence(&ctx, &code).is_err_and(
            |e| e.contains("exit code (0) differs from bash automation/97-ssot-lint.sh (1)")
        ));
        let order = native(tools.path(), "echo '[97-ssot-lint] ERROR: dead key' >&2\necho \"[97-ssot-lint] userenv: $MIOS_SSOT_LINT_ROOT/tools/lib/userenv.sh\"\necho '[97-ssot-lint] checked 1'\nexit 1\n")?;
        assert!(equivalence(&ctx, &order)
            .is_err_and(|e| e.contains("exit codes both 1); first difference at output line 1")));
        let text = native(tools.path(), &LEGACY.replace("checked 1", "checked 2"))?;
        let differs = equivalence(&ctx, &text).err().unwrap_or_default();
        assert!(
            differs.contains("output line 3") && differs.contains("rust: [97-ssot-lint] checked 2"),
            "{differs}"
        );
        Ok(())
    }

    #[test]
    fn normalisation_maps_every_spelling_of_the_actual_root() {
        let wsl = Path::new("/mnt/c/Work/MiOS");
        let text = "a /mnt/c/Work/MiOS/x\nb C:\\Work\\MiOS\\x\nc c:/Work/MiOS/x\n\n";
        assert_eq!(normalize(text, wsl), "a /ROOT/x\nb /ROOT/x\nc /ROOT/x");
        assert_eq!(
            normalize("D:\\MiOS\\y and /mnt/d/MiOS/y", Path::new("D:\\MiOS")),
            "/ROOT/y and /ROOT/y"
        );
        assert_eq!(
            normalize("/mnt/c/MiOS/y", Path::new("/var/tmp/other")),
            "/mnt/c/MiOS/y"
        );
        assert_eq!(normalize("/a/b\n", Path::new("/")), "/a/b");
    }
}
