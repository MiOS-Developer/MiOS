// AI-hint: Integration tests for mios-gate's credential-literals check, including the value-pinning defect a key-only register could not see.
// AI-related: src/mios-rs/mios-gate/src/credentials.rs, usr/share/mios/mios.toml, usr/share/containers/systemd

use std::fs;
use std::path::Path;
use std::process::Command;

fn bin() -> &'static str {
    env!("CARGO_BIN_EXE_mios-gate")
}

/// A minimal tree: one Quadlet with whatever Environment= lines the caller
/// asks for, plus the register.
fn tree(dir: &Path, unit_lines: &[&str], register: &[&str]) {
    fs::create_dir_all(dir.join("usr/share/containers/systemd")).unwrap();
    fs::create_dir_all(dir.join("usr/lib/systemd/system")).unwrap();
    fs::create_dir_all(dir.join("usr/share/mios")).unwrap();
    let body = format!(
        "[Unit]\nDescription=probe\n[Service]\n{}\n",
        unit_lines.join("\n")
    );
    fs::write(
        dir.join("usr/share/containers/systemd/probe.container"),
        body,
    )
    .unwrap();
    fs::write(dir.join("usr/lib/systemd/system/other.service"), "[Unit]\n").unwrap();
    let rows: String = register.iter().map(|r| format!("  \"{r}\",\n")).collect();
    fs::write(
        dir.join("usr/share/mios/mios.toml"),
        format!("[security.credential_literals]\ngrandfathered = [\n{rows}]\n"),
    )
    .unwrap();
}

fn run(dir: &Path) -> (i32, String) {
    let out = Command::new(bin())
        .args(["credential-literals", "--root"])
        .arg(dir)
        .output()
        .unwrap();
    let mut text = String::from_utf8_lossy(&out.stdout).to_string();
    text.push_str(&String::from_utf8_lossy(&out.stderr));
    (out.status.code().unwrap_or(-1), text)
}

const PIN: &str = "usr/share/containers/systemd/probe.container:POSTGRES_PASSWORD=mios";

#[test]
fn a_pinned_placeholder_is_clean() {
    let d = tempfile::tempdir().unwrap();
    tree(d.path(), &["Environment=POSTGRES_PASSWORD=mios"], &[PIN]);
    let (code, out) = run(d.path());
    assert_eq!(code, 0, "{out}");
}

/// The defect this port exists for. A key-only register grandfathers the KEY,
/// so an operator's real password baked in by a build-environment variable
/// reads as the same entry and the gate stays green.
#[test]
fn a_changed_value_on_a_grandfathered_key_fails() {
    let d = tempfile::tempdir().unwrap();
    tree(
        d.path(),
        &["Environment=POSTGRES_PASSWORD=NOT-A-REAL-PASSWORD-negative-test"],
        &[PIN],
    );
    let (code, out) = run(d.path());
    assert_eq!(code, 1, "{out}");
    assert!(out.contains("NOT-A-REAL-PASSWORD-negative-test"), "{out}");
    assert!(
        out.contains("shrink the list"),
        "the old pin must also read as gone: {out}"
    );
}

#[test]
fn a_new_credential_literal_fails() {
    let d = tempfile::tempdir().unwrap();
    tree(
        d.path(),
        &[
            "Environment=POSTGRES_PASSWORD=mios",
            "Environment=SOME_API_KEY=abcd1234",
        ],
        &[PIN],
    );
    let (code, out) = run(d.path());
    assert_eq!(code, 1, "{out}");
    assert!(out.contains("SOME_API_KEY=abcd1234"), "{out}");
}

/// A register entry that pins no value is the hole itself, so it is a finding.
#[test]
fn a_key_only_register_entry_fails() {
    let d = tempfile::tempdir().unwrap();
    tree(
        d.path(),
        &["Environment=POSTGRES_PASSWORD=mios"],
        &["usr/share/containers/systemd/probe.container:POSTGRES_PASSWORD"],
    );
    let (code, out) = run(d.path());
    assert_eq!(code, 1, "{out}");
    assert!(out.contains("pins no value"), "{out}");
}

#[test]
fn a_stale_register_entry_must_be_shrunk() {
    let d = tempfile::tempdir().unwrap();
    tree(
        d.path(),
        &["Environment=POSTGRES_PASSWORD=mios"],
        &[
            PIN,
            "usr/share/containers/systemd/gone.container:GONE_TOKEN=x",
        ],
    );
    let (code, out) = run(d.path());
    assert_eq!(code, 1, "{out}");
    assert!(out.contains("GONE_TOKEN"), "{out}");
}

/// Indirection through an env var or a systemd specifier is the sanctioned
/// pattern and must stay clean, or the gate pushes people back to literals.
#[test]
fn indirected_values_are_not_literals() {
    for v in ["${MIOS_PGVECTOR_PASS}", "%d/pgpass", ""] {
        let d = tempfile::tempdir().unwrap();
        tree(d.path(), &[&format!("Environment=SOME_SECRET={v}")], &[]);
        let (code, out) = run(d.path());
        assert_eq!(code, 0, "value {v:?} should not be a literal: {out}");
    }
}

/// Counters and feature flags are not credentials however they are spelled.
#[test]
fn counters_and_flags_are_not_credentials() {
    let d = tempfile::tempdir().unwrap();
    tree(
        d.path(),
        &[
            "Environment=MAX_TOKENS=4096",
            "Environment=CONTEXT_TOKENS=8192",
            "Environment=ENABLE_SECRET_ROTATION=true",
            "Environment=TOKEN_LIMIT=10",
            "Environment=NUM_TOKEN_BUCKETS=4",
        ],
        &[],
    );
    let (code, out) = run(d.path());
    assert_eq!(code, 0, "{out}");
}

/// Empty-Set Pass guard: a tree with no unit directories must not report
/// "no credential literals", it must report that it scanned nothing.
#[test]
fn an_empty_scan_cannot_run() {
    let d = tempfile::tempdir().unwrap();
    fs::create_dir_all(d.path().join("usr/share/mios")).unwrap();
    fs::write(
        d.path().join("usr/share/mios/mios.toml"),
        "[security.credential_literals]\ngrandfathered = []\n",
    )
    .unwrap();
    let (code, out) = run(d.path());
    assert_eq!(code, 2, "{out}");
    assert!(out.contains("nothing was compared"), "{out}");
}

#[test]
fn a_missing_or_malformed_ssot_cannot_run() {
    let d = tempfile::tempdir().unwrap();
    let (code, _) = run(d.path());
    assert_eq!(code, 2);

    let d2 = tempfile::tempdir().unwrap();
    tree(d2.path(), &["Environment=A=b"], &[]);
    fs::write(d2.path().join("usr/share/mios/mios.toml"), "[unclosed\n").unwrap();
    let (code2, out2) = run(d2.path());
    assert_eq!(code2, 2, "{out2}");
}

/// A register with no entries is legitimately clean when nothing is found --
/// that is different from a register that could not be read.
#[test]
fn an_empty_register_with_no_literals_is_clean() {
    let d = tempfile::tempdir().unwrap();
    tree(d.path(), &["Environment=LOG_LEVEL=debug"], &[]);
    let (code, out) = run(d.path());
    assert_eq!(code, 0, "{out}");
}

#[test]
fn secret_keys_below_min_floor_fails() {
    let d = tempfile::tempdir().unwrap();
    tree(d.path(), &["Environment=LOG_LEVEL=debug"], &[]);
    fs::write(
        d.path().join("usr/share/mios/mios.toml"),
        "[security.credential_literals]\ngrandfathered = []\n\n[security.secret_keys]\nmin_keys = 3\nkeys = [\"A\", \"B\"]\n",
    )
    .unwrap();
    let (code, out) = run(d.path());
    assert_eq!(code, 1, "{out}");
    assert!(out.contains("below min_keys floor"), "{out}");
}

#[test]
fn secret_keys_explicit_key_is_caught_as_literal() {
    let d = tempfile::tempdir().unwrap();
    tree(
        d.path(),
        &["Environment=CUSTOM_DISPATCH_GRANT=supersecret123"],
        &[],
    );
    fs::write(
        d.path().join("usr/share/mios/mios.toml"),
        "[security.credential_literals]\ngrandfathered = []\n\n[security.secret_keys]\nmin_keys = 1\nkeys = [\"CUSTOM_DISPATCH_GRANT\"]\n",
    )
    .unwrap();
    let (code, out) = run(d.path());
    assert_eq!(code, 1, "{out}");
    assert!(
        out.contains("CUSTOM_DISPATCH_GRANT=supersecret123"),
        "{out}"
    );
}
