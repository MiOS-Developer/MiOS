// AI-hint: Integration tests for mios-gate's profile-integrity check against synthetic mios.toml [profiles] tables.
// AI-related: src/mios-rs/mios-gate/src/profiles.rs, src/mios-rs/mios-build/src/lib.rs, usr/share/mios/mios.toml

use std::fs;
use std::path::Path;
use std::process::Command;

const BASE: &str = r#"
[build.phases]
list = [
  { ordinal = "01", name = "a", script = "01-a.sh", fatal = true, apply_class = "universal" },
  { ordinal = "02", name = "b", script = "02-b.sh", fatal = true, apply_class = "universal" },
]
[packages.a]
pkgs = []
[packages.s]
pkgs = []
[profiles]
default = "full"
[profiles.core]
floor = true
phases = ["a"]
package_sections = ["s"]
[profiles.full]
all = true
targets = ["oci"]
"#;

fn run(toml: &str) -> (i32, String) {
    let dir = tempfile::tempdir().unwrap();
    fs::create_dir_all(dir.path().join("usr/share/mios")).unwrap();
    fs::write(dir.path().join("usr/share/mios/mios.toml"), toml).unwrap();
    gate(dir.path())
}

fn gate(root: &Path) -> (i32, String) {
    let out = Command::new(env!("CARGO_BIN_EXE_mios-gate"))
        .args(["profile-integrity", "--root"])
        .arg(root)
        .output()
        .unwrap();
    let mut text = String::from_utf8_lossy(&out.stdout).to_string();
    text.push_str(&String::from_utf8_lossy(&out.stderr));
    (out.status.code().unwrap_or(-1), text)
}

#[test]
fn a_consistent_table_is_clean() {
    let (code, text) = run(BASE);
    assert_eq!(code, 0, "{text}");
}

#[test]
fn a_phase_typo_under_all_is_still_a_finding() {
    let (code, text) = run(&BASE.replace("all = true", "all = true\nphases = [\"zz-planted\"]"));
    assert_ne!(code, 0, "{text}");
    assert!(text.contains("zz-planted"), "{text}");
}

#[test]
fn a_section_cannot_stand_in_for_a_floor_phase_of_the_same_name() {
    // "dev" lacks the floor phase "a" but carries a package section also named "a".
    let toml = format!("{BASE}[profiles.dev]\npackage_sections = [\"s\", \"a\"]\n");
    let (code, text) = run(&toml);
    assert_ne!(code, 0, "{text}");
    assert!(
        text.contains("does not contain the floor") && text.contains("dev"),
        "{text}"
    );
}

#[test]
fn a_second_floor_and_no_floor_are_findings() {
    let (code, text) = run(&BASE.replace("all = true", "all = true\nfloor = true"));
    assert_ne!(code, 0, "{text}");
    assert!(
        text.contains("more than one profile declares floor"),
        "{text}"
    );
    let (code, text) = run(&BASE.replace("floor = true\n", ""));
    assert_ne!(code, 0, "{text}");
    assert!(
        text.contains("no profile in [profiles] declares floor"),
        "{text}"
    );
}

#[test]
fn a_floor_package_the_full_image_never_installs_is_a_finding() {
    // The floor names a section whose packages the `all` profile skips (enable = false):
    // a core image that would carry a package the full image lacks.
    let planted = BASE
        .replace(
            "[packages.s]\npkgs = []",
            "[packages.s]\npkgs = [\"kept\"]\n[packages.z]\nenable = false\npkgs = [\"zz-planted-pkg\"]",
        )
        .replace("package_sections = [\"s\"]", "package_sections = [\"s\", \"z\"]");
    let (code, text) = run(&planted);
    assert_ne!(code, 0, "{text}");
    assert!(text.contains("zz-planted-pkg"), "{text}");
    assert!(text.contains("enable = false"), "{text}");
    // The same section enabled is a bigger core the full image also installs: clean.
    let (code, text) = run(&planted.replace("enable = false\n", ""));
    assert_eq!(code, 0, "{text}");
}

#[test]
fn a_required_section_is_part_of_the_floor_closure() {
    // requires_sections pulls a section's packages in, as packages.sh does. Enabled,
    // the dependency's packages count for both images: clean. Disabled, packages.sh
    // refuses the parent section outright, so the gate names the pair.
    let required = BASE.replace(
        "[packages.s]\npkgs = []",
        "[packages.s]\npkgs = []\nrequires_sections = [\"q\"]\n[packages.q]\npkgs = [\"qq-dep-pkg\"]",
    );
    let (code, text) = run(&required);
    assert_eq!(code, 0, "{text}");
    let (code, text) = run(&required.replace("[packages.q]\n", "[packages.q]\nenable = false\n"));
    assert_ne!(code, 0, "{text}");
    assert!(text.contains("requires disabled section \"q\""), "{text}");
}
