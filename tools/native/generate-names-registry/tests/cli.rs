// AI-hint: Two-sided test of the generate-names-registry shim: it resolves the root (MIOS_DRIFT_ROOT, else the working directory), projects the names registry there, and fails without writing when the root has no SSOT.
// AI-related: tools/native/generate-names-registry/src/main.rs, tools/native/mios-gen/src/lib.rs

use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

/// A scratch directory removed on drop; std only, so the shim gains no
/// dependency just to be tested.
struct Scratch(PathBuf);

impl Scratch {
    fn new(tag: &str) -> Self {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or_default();
        let dir = std::env::temp_dir().join(format!("gnr-{tag}-{}-{nanos}", std::process::id()));
        fs::create_dir_all(&dir).expect("create scratch directory");
        Scratch(dir)
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

const NAMES: &str = "usr/share/mios/names.generated.txt";
const REFERENCES: &str = "usr/share/mios/referenced_names.txt";

fn shim(cwd: &Path, drift_root: Option<&Path>) -> Output {
    let mut command = Command::new(env!("CARGO_BIN_EXE_generate-names-registry"));
    command.current_dir(cwd).env_remove("MIOS_DRIFT_ROOT");
    if let Some(root) = drift_root {
        command.env("MIOS_DRIFT_ROOT", root);
    }
    command.output().expect("run generate-names-registry")
}

fn git(root: &Path, args: &[&str]) {
    let status = Command::new("git")
        .arg("-C")
        .arg(root)
        .args(args)
        .status()
        .expect("git must be installed to build the census fixture");
    assert!(
        status.success(),
        "git {args:?} failed in {}",
        root.display()
    );
}

/// A minimal MiOS tree: one SSOT key and one tracked consumer of it. The census
/// reads `git ls-files`, so the fixture is a real repository.
fn tree() -> Scratch {
    let tree = Scratch::new("tree");
    fs::create_dir_all(tree.0.join("usr/share/mios")).unwrap();
    fs::write(
        tree.0.join("usr/share/mios/mios.toml"),
        "[ports]\nhttp = 80\n",
    )
    .unwrap();
    fs::write(
        tree.0.join("serve.sh"),
        "#!/bin/sh\nexec httpd --port \"${MIOS_PORTS_HTTP}\"\n",
    )
    .unwrap();
    git(&tree.0, &["init", "-q"]);
    git(&tree.0, &["add", "usr/share/mios/mios.toml", "serve.sh"]);
    tree
}

#[test]
fn a_root_without_the_ssot_fails_and_writes_nothing() {
    let empty = Scratch::new("empty");
    let out = shim(&empty.0, Some(&empty.0));
    assert!(!out.status.success(), "a root with no mios.toml must fail");
    assert!(
        String::from_utf8_lossy(&out.stderr).contains("mios.toml not found"),
        "the refusal must say why: {}",
        String::from_utf8_lossy(&out.stderr)
    );
    assert!(!empty.0.join(NAMES).exists());
    assert!(!empty.0.join(REFERENCES).exists());
}

#[test]
fn the_working_directory_is_the_root_when_mios_drift_root_is_unset() {
    let tree = tree();
    let out = shim(&tree.0, None);
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    assert_eq!(
        fs::read_to_string(tree.0.join(NAMES)).unwrap(),
        "ports.http  MIOS_PORTS_HTTP\n"
    );
    assert_eq!(
        fs::read_to_string(tree.0.join(REFERENCES)).unwrap(),
        "MIOS_PORTS_HTTP\n"
    );
}

#[test]
fn mios_drift_root_wins_over_the_working_directory() {
    let tree = tree();
    let elsewhere = Scratch::new("cwd");
    // The valid tree is named only by MIOS_DRIFT_ROOT: it must be the one written.
    let out = shim(&elsewhere.0, Some(&tree.0));
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    assert!(tree.0.join(NAMES).is_file());
    assert!(!elsewhere.0.join(NAMES).exists());
    // And the other way round: an SSOT-less MIOS_DRIFT_ROOT fails even from
    // inside a valid tree, rather than falling back to the working directory.
    let before = fs::read_to_string(tree.0.join(NAMES)).unwrap();
    let out = shim(&tree.0, Some(&elsewhere.0));
    assert!(!out.status.success());
    assert_eq!(fs::read_to_string(tree.0.join(NAMES)).unwrap(), before);
}

/// --check is the gate's entry: it names a drifted artefact and writes nothing.
#[test]
fn check_mode_reports_drift_without_writing() {
    let tree = tree();
    assert!(shim(&tree.0, Some(&tree.0)).status.success());
    let check = |root: &Path| {
        let mut command = Command::new(env!("CARGO_BIN_EXE_generate-names-registry"));
        command
            .arg("--check")
            .env("MIOS_DRIFT_ROOT", root)
            .output()
            .unwrap()
    };
    assert!(check(&tree.0).status.success());
    fs::write(tree.0.join(NAMES), "planted\n").unwrap();
    let out = check(&tree.0);
    assert!(!out.status.success());
    assert!(String::from_utf8_lossy(&out.stderr).contains(NAMES));
    assert_eq!(fs::read_to_string(tree.0.join(NAMES)).unwrap(), "planted\n");
}
