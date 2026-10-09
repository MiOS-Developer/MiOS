// AI-hint: Execute the real native progress CLI and build adapter against positive, missing, warning and swallowed-failure controls.
// AI-related: /usr/share/mios/templates/rust, automation/build.sh, src/mios-rs/mios-build/src/progress.rs
use std::io::Write;
use std::path::Path;
use std::process::{Command, Stdio};

fn cli(
    root: &Path,
    state: &Path,
    event: &str,
    extra: &[&str],
    input: Option<&str>,
) -> Result<std::process::Output, Box<dyn std::error::Error>> {
    let mut child = Command::new(env!("CARGO_BIN_EXE_miosd"))
        .args(["build-progress", "--root"])
        .arg(root)
        .arg("--state")
        .arg(state)
        .args(["--event", event])
        .args(extra)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;
    if let Some(input) = input {
        child
            .stdin
            .take()
            .ok_or("missing CLI stdin")?
            .write_all(input.as_bytes())?;
    }
    drop(child.stdin.take());
    Ok(child.wait_with_output()?)
}

fn fixture(root: &Path) -> Result<(), Box<dyn std::error::Error>> {
    std::fs::create_dir_all(root.join("usr/share/mios"))?;
    std::fs::write(
        root.join("usr/share/mios/mios.toml"),
        "[pipeline.console]\nwidth=100\nbar_width=24\n",
    )?;
    Ok(())
}

#[test]
fn actual_cli_is_atomic_rejects_duplicates_and_refuses_incomplete_success(
) -> Result<(), Box<dyn std::error::Error>> {
    let temp = tempfile::tempdir()?;
    fixture(temp.path())?;
    let state = temp.path().join("state.json");
    assert!(
        cli(temp.path(), &state, "init", &[], Some("first\nsecond\n"))?
            .status
            .success()
    );
    assert!(!cli(temp.path(), &state, "init", &[], Some("overwrite\n"))?
        .status
        .success());
    assert!(!state.with_extension("lock").exists());
    let before = std::fs::read(&state)?;
    assert!(!cli(
        temp.path(),
        &state,
        "result",
        &["--name", "first", "--status", "pass"],
        None
    )?
    .status
    .success());
    assert_eq!(before, std::fs::read(&state)?);
    assert!(!cli(temp.path(), &state, "finish", &[], None)?
        .status
        .success());
    for name in ["first", "second"] {
        assert!(cli(temp.path(), &state, "start", &["--name", name], None)?
            .status
            .success());
        assert!(cli(
            temp.path(),
            &state,
            "result",
            &["--name", name, "--status", "pass"],
            None
        )?
        .status
        .success());
    }
    let result = cli(temp.path(), &state, "finish", &[], None)?;
    assert!(result.status.success());
    assert!(String::from_utf8(result.stdout)?.contains("2/2 100%"));
    assert!(!state.with_extension("lock").exists());
    Ok(())
}

#[test]
fn actual_ssot_post_plan_preserves_operator_names_and_rejects_missing_or_injected_gates(
) -> Result<(), Box<dyn std::error::Error>> {
    let temp = tempfile::tempdir()?;
    fixture(temp.path())?;
    let path = temp.path().join("usr/share/mios/mios.toml");
    let source = include_str!("../../../../usr/share/mios/mios.toml");
    let invoke = || {
        Command::new(env!("CARGO_BIN_EXE_miosd"))
            .args(["build", "--post-list"])
            .env("MIOS_ROOT", temp.path())
            .output()
    };
    std::fs::write(&path, source)?;
    let baseline = invoke()?;
    assert!(
        baseline.status.success(),
        "{}",
        String::from_utf8_lossy(&baseline.stderr)
    );
    assert_eq!(String::from_utf8(baseline.stdout)?.lines().count(), 10);
    std::fs::write(
        &path,
        source.replace(
            "name = \"post-package-health\"",
            "name = \"operator-package-health\"",
        ),
    )?;
    let changed = invoke()?;
    assert!(changed.status.success());
    assert!(String::from_utf8(changed.stdout)?.contains("operator-package-health:package_health"));
    for modified in [
        source.replace("{ name = \"98-drift-checks.sh\", action = \"drift\" },", ""),
        source.replace("action = \"drift\"", "action = \"drift; touch injected\""),
        source.replace("name = \"post-package-health\"", "name = \"bad:identity\""),
        source.replace("action = \"drift\"", "action = \"ssot\""),
    ] {
        std::fs::write(&path, modified)?;
        assert!(!invoke()?.status.success());
    }
    Ok(())
}

#[cfg(unix)]
#[test]
fn real_shell_adapter_cannot_swallow_gate_failures_or_missing_dependencies(
) -> Result<(), Box<dyn std::error::Error>> {
    let temp = tempfile::tempdir()?;
    fixture(temp.path())?;
    let source = include_str!("../../../../automation/build.sh");
    let start = source
        .find("_finding() {")
        .ok_or("missing native adapter")?;
    let end = source[start..]
        .find("\n[[ -n \"$_miosd\"")
        .ok_or("missing adapter boundary")?
        + start;
    let adapter = &source[start..end];
    let script = format!(
        r#"set -euo pipefail
_miosd=$1
_mios_root=$2
PROGRESS_DIR=$2
PROGRESS_STATE="$2/state.json"
SCRIPT_COUNT=0
FAIL_LOG=()
WARN_LOG=()
WARNED_JSON=()
declare -A PHASE_FATAL=([warn]=false)
{adapter}
bad_gate() {{ false; echo 'SWALLOWED_FAILURE'; return 0; }}
warn_gate() {{ return 7; }}
missing_gate() {{ _finding missing 'required test interpreter absent'; return 125; }}
printf '%s\n' good bad warn missing | "$_miosd" build-progress --root "$_mios_root" --state "$PROGRESS_STATE" --event init
_run_stage good true
_run_stage bad bad_gate
_run_stage warn warn_gate
_run_stage missing missing_gate
"$_miosd" build-progress --root "$_mios_root" --state "$PROGRESS_STATE" --event finish
"#
    );
    let output = Command::new("bash")
        .args([
            "-c",
            &script,
            "mios-build-control",
            env!("CARGO_BIN_EXE_miosd"),
        ])
        .arg(temp.path())
        .output()?;
    let stdout = String::from_utf8(output.stdout)?;
    assert!(!output.status.success(), "{stdout}");
    assert!(!stdout.contains("SWALLOWED_FAILURE"));
    assert!(stdout.contains("4/4 100%"));
    assert!(
        stdout.contains("PASS 1 FAIL 1 WARN 1 SKIP 0 MISSING 1 PENDING 0"),
        "{stdout}"
    );
    let state: serde_json::Value =
        serde_json::from_slice(&std::fs::read(temp.path().join("state.json"))?)?;
    assert_eq!(
        state["completed"]
            .as_array()
            .ok_or("outcomes omitted")?
            .len(),
        4
    );
    assert_eq!(state["notes"][0][0], "missing");
    Ok(())
}
