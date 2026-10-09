// AI-hint: Asserts an image (or, with --allow-tree-only, a staged tree) holds every component [testing.smoke_components] names for its profile: the floor plus the section, phase and profile overlays the profile selects through mios_build Profiles/PhaseRegistry.
// AI-related: usr/share/mios/mios.toml, src/mios-rs/mios-build/src/lib.rs, tools/drift-checks.py, tests/test-image-equivalence.sh, tests/bake-smoke.sh
// AI-functions: check

use crate::Report;
use mios_build::{PhaseRegistry, Profiles};
use std::collections::{BTreeSet, VecDeque};
use std::path::{Path, PathBuf};
use std::process::Command;

const CHECK: &str = "image-equivalence";
const SSOT: &str = "usr/share/mios/mios.toml";
/// Where the image records the profile it was built as (task_3465 writes both).
const RECORDS: [&str; 2] = ["usr/lib/mios/version", "usr/lib/os-release"];
const RECORD_KEY: &str = "MIOS_PROFILES_DEFAULT";
/// The directories a login PATH searches, relative to the asserted root.
const PATH_DIRS: [&str; 6] = [
    "usr/bin",
    "usr/sbin",
    "usr/local/bin",
    "usr/local/sbin",
    "bin",
    "sbin",
];
/// Where an rpm database lives under a root; neither present means no rpm query can answer.
const RPMDB_DIRS: [&str; 2] = ["usr/lib/sysimage/rpm", "var/lib/rpm"];
/// The keys of [testing.smoke_components] that hold overlays rather than probes.
const OVERLAYS: [&str; 3] = ["sections", "phases", "profiles"];

/// How one probe kind is asserted against the root.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
enum Kind {
    /// Present under the root, following symlinks inside it.
    Exists,
    /// A regular file under the root.
    File,
    /// An executable regular file in one of PATH_DIRS.
    Command,
    /// A package the root's rpm database provides.
    Rpm,
}

/// The probe-kind keys a manifest table may carry. Anything else is a typo,
/// and a typo is an assertion nobody runs.
const KINDS: [(&str, Kind); 7] = [
    ("shims", Kind::Exists),
    ("units", Kind::File),
    ("python_entries", Kind::File),
    ("manpages", Kind::File),
    ("paths", Kind::Exists),
    ("commands", Kind::Command),
    ("rpm_sections", Kind::Rpm),
];

#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
struct Probe {
    kind: Kind,
    item: String,
    origin: String,
}

impl Probe {
    fn label(&self) -> String {
        match self.kind {
            Kind::Command => format!("command {}", self.item),
            Kind::Rpm => format!("rpm {}", self.item),
            Kind::Exists | Kind::File => self.item.clone(),
        }
    }
}

/// What the caller asked for. `ssot` defaults to the root's own baked SSOT.
pub struct Options {
    pub root: PathBuf,
    pub ssot: Option<PathBuf>,
    pub profile: Option<String>,
    pub allow_tree_only: bool,
}

fn cannot_run(why: impl Into<String>) -> Report {
    Report {
        check: CHECK.to_string(),
        ok: false,
        could_not_run: Some(why.into()),
        summary: String::new(),
        findings: Vec::new(),
    }
}

/// Resolve `rel` under `root` the way the image itself would: symlinks are
/// followed, an absolute target restarts at `root`, and `..` never climbs out.
/// None when any component is absent or the chain loops.
fn resolve_in_root(root: &Path, rel: &str) -> Option<PathBuf> {
    let split = |s: &str| -> Vec<String> {
        s.split('/')
            .filter(|c| !c.is_empty() && *c != ".")
            .map(str::to_string)
            .collect()
    };
    let mut pending: VecDeque<String> = split(rel).into();
    let mut cur = root.to_path_buf();
    let mut hops = 0u32;
    while let Some(c) = pending.pop_front() {
        if c == ".." {
            if cur != root {
                cur.pop();
            }
            continue;
        }
        let next = cur.join(&c);
        let md = std::fs::symlink_metadata(&next).ok()?;
        if md.file_type().is_symlink() {
            hops += 1;
            if hops > 40 {
                return None;
            }
            let target = std::fs::read_link(&next).ok()?;
            let target = target.to_string_lossy().to_string();
            if target.starts_with('/') {
                cur = root.to_path_buf();
            }
            for part in split(&target).into_iter().rev() {
                pending.push_front(part);
            }
        } else {
            cur = next;
        }
    }
    Some(cur)
}

fn is_executable(md: &std::fs::Metadata) -> bool {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        md.is_file() && md.permissions().mode() & 0o111 != 0
    }
    #[cfg(not(unix))]
    {
        md.is_file()
    }
}

fn present(root: &Path, kind: Kind, item: &str) -> bool {
    match kind {
        Kind::Exists => resolve_in_root(root, item).is_some(),
        Kind::File => resolve_in_root(root, item)
            .and_then(|p| std::fs::metadata(p).ok())
            .is_some_and(|m| m.is_file()),
        Kind::Command => PATH_DIRS.iter().any(|d| {
            resolve_in_root(root, &format!("{d}/{item}"))
                .and_then(|p| std::fs::metadata(p).ok())
                .is_some_and(|m| is_executable(&m))
        }),
        // Rpm probes are answered in a batch by rpm_missing.
        Kind::Rpm => false,
    }
}

/// The value the root records for MIOS_PROFILES_DEFAULT, per record file.
fn recorded_profiles(root: &Path) -> Vec<(String, String)> {
    let mut out = Vec::new();
    for rec in RECORDS {
        let Some(p) = resolve_in_root(root, rec) else {
            continue;
        };
        let Ok(text) = std::fs::read_to_string(p) else {
            continue;
        };
        for line in text.lines() {
            if let Some(v) = line.trim().strip_prefix(&format!("{RECORD_KEY}=")) {
                out.push((rec.to_string(), v.trim().trim_matches('"').to_string()));
            }
        }
    }
    out
}

fn strings(v: &toml::Value, at: &str) -> Result<Vec<String>, String> {
    let arr = v
        .as_array()
        .ok_or_else(|| format!("[testing.smoke_components]{at} is not a list"))?;
    arr.iter()
        .map(|x| {
            x.as_str()
                .map(str::to_string)
                .ok_or_else(|| format!("[testing.smoke_components]{at} holds a non-string"))
        })
        .collect()
}

/// The probes one manifest table declares. Overlay keys are skipped only in
/// the floor table; everywhere else they, like any unknown key, are an error.
fn probes_of(
    table: &toml::value::Table,
    at: &str,
    origin: &str,
    packages: Option<&toml::value::Table>,
    is_floor: bool,
) -> Result<Vec<Probe>, String> {
    let mut out = Vec::new();
    for (key, val) in table {
        if is_floor && OVERLAYS.contains(&key.as_str()) {
            continue;
        }
        let Some((_, kind)) = KINDS.iter().find(|(k, _)| k == key) else {
            return Err(format!(
                "[testing.smoke_components]{at}.{key} is not a probe kind (one of {})",
                KINDS.iter().map(|(k, _)| *k).collect::<Vec<_>>().join(", ")
            ));
        };
        for item in strings(val, &format!("{at}.{key}"))? {
            if *kind != Kind::Rpm {
                out.push(Probe {
                    kind: *kind,
                    item,
                    origin: origin.to_string(),
                });
                continue;
            }
            let pkgs = packages
                .and_then(|p| p.get(&item))
                .and_then(|s| s.get("pkgs"))
                .ok_or_else(|| {
                    format!(
                        "[testing.smoke_components]{at}.rpm_sections names {item:?}, \
                         which declares no [packages.{item}].pkgs"
                    )
                })?;
            for pkg in strings(
                pkgs,
                &format!("{at}.rpm_sections -> [packages.{item}].pkgs"),
            )? {
                out.push(Probe {
                    kind: Kind::Rpm,
                    item: pkg,
                    origin: origin.to_string(),
                });
            }
        }
    }
    Ok(out)
}

/// The rpm probes whose package the root's database does not provide.
fn rpm_missing(root: &Path, probes: &[&Probe]) -> Result<Vec<String>, String> {
    let mut missing = Vec::new();
    for p in probes {
        let out = Command::new("rpm")
            .arg("--root")
            .arg(root)
            .args(["-q", "--whatprovides", &p.item])
            .output()
            .map_err(|e| format!("rpm could not be run: {e}"))?;
        if !out.status.success() {
            missing.push(p.item.clone());
        }
    }
    Ok(missing)
}

fn int_at(v: &toml::Value, path: &[&str]) -> Option<i64> {
    path.iter()
        .try_fold(v, |acc, k| acc.get(*k))
        .and_then(|x| x.as_integer())
}

pub fn check(opts: &Options) -> Report {
    let Some(profile) = opts.profile.as_deref() else {
        return cannot_run("--profile is required: equivalence is asserted for a named profile");
    };

    // An image is asserted from inside itself; anything else is a staged tree
    // and must say so, or a host's /usr would pass for the image's.
    let root = match std::fs::canonicalize(&opts.root) {
        Ok(r) => r,
        Err(e) => return cannot_run(format!("root {} is unreadable: {e}", opts.root.display())),
    };
    if root != Path::new("/") && !opts.allow_tree_only {
        return cannot_run(format!(
            "root {} is not / -- an image is asserted from inside itself; \
             pass --allow-tree-only to assert a staged tree",
            root.display()
        ));
    }

    let baked = root.join(SSOT);
    let ssot = opts.ssot.clone().unwrap_or_else(|| baked.clone());
    let Ok(text) = std::fs::read_to_string(&ssot) else {
        return cannot_run(format!("{} could not be read", ssot.display()));
    };
    let Ok(val) = text.parse::<toml::Value>() else {
        return cannot_run(format!("{} did not parse", ssot.display()));
    };

    let profiles = match Profiles::from_toml_str(&text) {
        Ok(p) => p,
        Err(e) => return cannot_run(e),
    };
    if !profiles.names().iter().any(|n| n == profile) {
        return cannot_run(format!("profile {profile:?} is not declared in [profiles]"));
    }
    let (resolved, closure) = match (profiles.resolve(profile), profiles.closure(profile)) {
        (Ok(r), Ok(c)) => (r, c),
        (Err(e), _) | (_, Err(e)) => return cannot_run(e),
    };
    let registry = match PhaseRegistry::load_from_toml(&ssot) {
        Ok(r) => r,
        Err(e) => return cannot_run(e.to_string()),
    };
    let phases: Vec<String> = match registry.for_profile(&resolved) {
        Ok(p) => p.into_iter().map(|x| x.name).collect(),
        Err(e) => return cannot_run(e),
    };
    let packages = val.get("packages").and_then(|v| v.as_table());
    let sections: BTreeSet<String> = if resolved.all {
        packages
            .map(|t| {
                t.iter()
                    .filter(|(_, v)| v.is_table())
                    .map(|(k, _)| k.clone())
                    .collect()
            })
            .unwrap_or_default()
    } else {
        resolved.package_sections.clone()
    };

    let empty = toml::value::Table::new();
    let manifest = val
        .get("testing")
        .and_then(|t| t.get("smoke_components"))
        .and_then(|t| t.as_table())
        .unwrap_or(&empty);
    let floor = match probes_of(manifest, "", "floor", packages, true) {
        Ok(f) => f,
        Err(e) => return cannot_run(e),
    };

    let mut overlays: Vec<(String, Vec<Probe>)> = Vec::new();
    let selected = [
        (
            "sections",
            "section",
            sections.iter().cloned().collect::<Vec<_>>(),
        ),
        ("phases", "phase", phases),
        ("profiles", "profile", closure.clone()),
    ];
    for (key, tag, names) in selected {
        let Some(group) = manifest.get(key) else {
            continue;
        };
        let Some(group) = group.as_table() else {
            return cannot_run(format!("[testing.smoke_components].{key} is not a table"));
        };
        for name in names {
            let Some(t) = group.get(&name) else { continue };
            let Some(t) = t.as_table() else {
                return cannot_run(format!(
                    "[testing.smoke_components.{key}.{name}] is not a table"
                ));
            };
            let origin = format!("{tag}:{name}");
            match probes_of(t, &format!(".{key}.{name}"), &origin, packages, false) {
                Ok(p) => overlays.push((origin, p)),
                Err(e) => return cannot_run(e),
            }
        }
    }

    let total = floor.len() + overlays.iter().map(|(_, p)| p.len()).sum::<usize>();
    if total == 0 {
        return cannot_run(format!(
            "no assertions in [testing.smoke_components] of {}",
            ssot.display()
        ));
    }

    // Grow-only, anchored twice: the caller's floor may not sit below the
    // caller's own minimum, and the caller's minimum may not sit below the one
    // the image baked -- a scratch SSOT cannot lower what the image promised.
    let Some(min) = int_at(&val, &["testing", "min_smoke_components"]) else {
        return cannot_run("[testing].min_smoke_components is absent; the floor has no minimum");
    };
    if ssot != baked {
        let Some(baked_min) = std::fs::read_to_string(&baked)
            .ok()
            .and_then(|t| t.parse::<toml::Value>().ok())
            .and_then(|v| int_at(&v, &["testing", "min_smoke_components"]))
        else {
            return cannot_run(format!(
                "{} carries no [testing].min_smoke_components to anchor the grow-only floor",
                baked.display()
            ));
        };
        if min < baked_min {
            return cannot_run(format!(
                "min_smoke_components {min} is below the floor {baked_min} (grow-only)"
            ));
        }
    }
    if (floor.len() as i64) < min {
        return cannot_run(format!(
            "the floor asserts {} component(s), below [testing].min_smoke_components {min} (grow-only)",
            floor.len()
        ));
    }

    // One probe per (kind, item): the first origin that names it owns it.
    let mut seen: BTreeSet<(Kind, String)> = BTreeSet::new();
    let all: Vec<Probe> = floor
        .iter()
        .chain(overlays.iter().flat_map(|(_, p)| p.iter()))
        .filter(|p| seen.insert((p.kind, p.item.clone())))
        .cloned()
        .collect();

    let mut findings = Vec::new();
    let rpm: Vec<&Probe> = all.iter().filter(|p| p.kind == Kind::Rpm).collect();
    let has_rpmdb = RPMDB_DIRS
        .iter()
        .any(|d| resolve_in_root(&root, d).is_some_and(|p| p.is_dir()));
    let mut deferred = 0usize;
    if !rpm.is_empty() {
        let missing = if has_rpmdb {
            rpm_missing(&root, &rpm)
        } else {
            Err(format!(
                "{} carries no rpm database ({})",
                root.display(),
                RPMDB_DIRS.join(" or ")
            ))
        };
        match missing {
            Ok(m) => {
                for p in rpm.iter().filter(|p| m.contains(&p.item)) {
                    findings.push(format!("{}: {} missing", p.origin, p.label()));
                }
            }
            // A staged tree has no package database by construction; the
            // deferral is counted and printed, never folded into "clean".
            Err(_) if opts.allow_tree_only => deferred = rpm.len(),
            Err(e) => return cannot_run(format!("{} rpm probe(s) cannot run: {e}", rpm.len())),
        }
    }
    for p in all.iter().filter(|p| p.kind != Kind::Rpm) {
        if !present(&root, p.kind, &p.item) {
            findings.push(format!("{}: {} missing", p.origin, p.label()));
        }
    }

    let records = recorded_profiles(&root);
    for (file, rec) in &records {
        if rec != profile {
            findings.push(format!(
                "image declares {RECORD_KEY}={rec} ({file}), caller asserts {profile}"
            ));
        }
    }

    let overlay_text = if overlays.is_empty() {
        "none".to_string()
    } else {
        overlays
            .iter()
            .map(|(o, p)| format!("{o} {}", p.len()))
            .collect::<Vec<_>>()
            .join(", ")
    };
    let record_text = if records.is_empty() {
        format!("no {RECORD_KEY} recorded")
    } else {
        format!("{RECORD_KEY} recorded as {profile}")
    };
    let deferred_text = if deferred > 0 {
        format!("; {deferred} rpm probe(s) NOT run (tree-only root has no rpm database)")
    } else {
        String::new()
    };
    Report {
        check: CHECK.to_string(),
        ok: findings.is_empty(),
        could_not_run: None,
        summary: format!(
            "clean -- profile {profile} (closure {}): floor {} probe(s) (min {min}), overlays: {overlay_text}; \
             {} asserted of {} declared; {record_text}{deferred_text}",
            closure.join(" > "),
            floor.len(),
            all.len() - deferred,
            total,
        ),
        findings,
    }
}

#[cfg(test)]
mod tests {
    // Test code: a panic here IS the assertion.
    #![allow(clippy::unwrap_used, clippy::expect_used, clippy::panic)]
    use super::*;

    const SSOT_T: &str = r#"
[build.phases]
list = [
  { ordinal = "01", name = "overlay", script = "01-overlay.sh", fatal = true, apply_class = "universal" },
  { ordinal = "02", name = "gpu", script = "02-gpu.sh", fatal = true, apply_class = "universal" },
]
[packages.base]
pkgs = ["bash"]
[packages.devcontainer]
pkgs = ["just"]
[profiles]
default = "full"
[profiles.core]
floor = true
phases = ["overlay"]
package_sections = ["base"]
[profiles.dev]
extends = ["core"]
package_sections = ["devcontainer"]
[profiles.full]
all = true
[testing]
min_smoke_components = 3
[testing.smoke_components]
shims = ["usr/libexec/mios/mios-doctor"]
units = ["usr/lib/systemd/system/mios.service"]
commands = ["podman"]
[testing.smoke_components.profiles.dev]
paths = ["usr/share/dev-only"]
[testing.smoke_components.phases.gpu]
paths = ["usr/share/gpu-only"]
"#;

    struct Fx {
        dir: tempfile::TempDir,
    }

    impl Fx {
        fn new() -> Self {
            let dir = tempfile::tempdir().unwrap();
            let r = dir.path();
            for f in [
                "usr/libexec/mios/mios-doctor",
                "usr/lib/systemd/system/mios.service",
                "usr/share/dev-only",
                "usr/share/gpu-only",
            ] {
                std::fs::create_dir_all(r.join(f).parent().unwrap()).unwrap();
                std::fs::write(r.join(f), "x").unwrap();
            }
            std::fs::create_dir_all(r.join("usr/bin")).unwrap();
            std::fs::write(r.join("usr/bin/podman"), "#!/bin/sh\n").unwrap();
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                std::fs::set_permissions(
                    r.join("usr/bin/podman"),
                    std::fs::Permissions::from_mode(0o755),
                )
                .unwrap();
                std::os::unix::fs::symlink("usr/bin", r.join("bin")).unwrap();
            }
            std::fs::create_dir_all(r.join("usr/share/mios")).unwrap();
            std::fs::write(r.join(SSOT), SSOT_T).unwrap();
            Fx { dir }
        }

        fn run(&self, profile: &str) -> Report {
            self.run_with(profile, None, true)
        }

        fn run_with(&self, profile: &str, ssot: Option<PathBuf>, tree: bool) -> Report {
            check(&Options {
                root: self.dir.path().to_path_buf(),
                ssot,
                profile: Some(profile.to_string()),
                allow_tree_only: tree,
            })
        }

        fn scratch(&self, text: &str) -> PathBuf {
            let p = self.dir.path().join("scratch.toml");
            std::fs::write(&p, text).unwrap();
            p
        }
    }

    #[test]
    fn image_equivalence_clean_core_has_no_dev_overlay() {
        let r = Fx::new().run("core");
        assert!(r.ok, "{:?} {:?}", r.findings, r.could_not_run);
        assert!(r.summary.starts_with("clean"), "{}", r.summary);
        assert!(r.summary.contains("overlays: none"), "{}", r.summary);
    }

    #[test]
    fn image_equivalence_dev_adds_its_overlay_and_full_its_phases() {
        let fx = Fx::new();
        let dev = fx.run("dev");
        assert!(
            dev.ok && dev.summary.contains("profile:dev 1"),
            "{}",
            dev.summary
        );
        assert!(
            dev.summary.contains("closure core > dev"),
            "{}",
            dev.summary
        );
        let full = fx.run("full");
        assert!(
            full.ok && full.summary.contains("phase:gpu 1"),
            "{}",
            full.summary
        );
        assert!(!full.summary.contains("profile:dev"), "{}", full.summary);
    }

    #[test]
    fn image_equivalence_names_each_miss_with_its_origin() {
        let fx = Fx::new();
        std::fs::remove_file(fx.dir.path().join("usr/libexec/mios/mios-doctor")).unwrap();
        std::fs::remove_file(fx.dir.path().join("usr/share/dev-only")).unwrap();
        let r = fx.run("dev");
        assert_eq!(r.code(), crate::EXIT_VIOLATIONS);
        assert!(r
            .findings
            .contains(&"floor: usr/libexec/mios/mios-doctor missing".to_string()));
        assert!(r
            .findings
            .contains(&"profile:dev: usr/share/dev-only missing".to_string()));
    }

    #[cfg(unix)]
    #[test]
    fn image_equivalence_command_must_be_executable_and_symlinks_stay_in_root() {
        let fx = Fx::new();
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(
                fx.dir.path().join("usr/bin/podman"),
                std::fs::Permissions::from_mode(0o644),
            )
            .unwrap();
            let r = fx.run("core");
            assert!(
                r.findings
                    .contains(&"floor: command podman missing".to_string()),
                "{:?}",
                r.findings
            );
            // An absolute symlink resolves inside the root, never on the host.
            std::fs::remove_file(fx.dir.path().join("usr/libexec/mios/mios-doctor")).unwrap();
            std::os::unix::fs::symlink(
                "/usr/lib/systemd/system/mios.service",
                fx.dir.path().join("usr/libexec/mios/mios-doctor"),
            )
            .unwrap();
            assert!(resolve_in_root(fx.dir.path(), "usr/libexec/mios/mios-doctor").is_some());
            assert!(resolve_in_root(fx.dir.path(), "bin/podman").is_some());
            assert!(resolve_in_root(fx.dir.path(), "../../../etc/passwd").is_none());
        }
    }

    #[test]
    fn image_equivalence_refuses_an_undeclared_profile() {
        let r = Fx::new().run("zz-planted");
        assert_eq!(r.code(), crate::EXIT_CANNOT_RUN);
        assert_eq!(
            r.could_not_run.as_deref(),
            Some("profile \"zz-planted\" is not declared in [profiles]")
        );
    }

    #[test]
    fn image_equivalence_refuses_an_empty_manifest() {
        let fx = Fx::new();
        let cut = SSOT_T
            .split("[testing.smoke_components]")
            .next()
            .unwrap()
            .to_string()
            + "[testing.smoke_components]\n";
        let r = fx.run_with("core", Some(fx.scratch(&cut)), true);
        assert_eq!(r.code(), crate::EXIT_CANNOT_RUN);
        assert!(r
            .could_not_run
            .unwrap()
            .starts_with("no assertions in [testing.smoke_components]"));
    }

    #[test]
    fn image_equivalence_floor_is_grow_only() {
        let fx = Fx::new();
        let lowered = SSOT_T.replace("min_smoke_components = 3", "min_smoke_components = 2");
        let r = fx.run_with("core", Some(fx.scratch(&lowered)), true);
        assert_eq!(
            r.could_not_run.as_deref(),
            Some("min_smoke_components 2 is below the floor 3 (grow-only)")
        );
        let raised = SSOT_T.replace("min_smoke_components = 3", "min_smoke_components = 4");
        let r = fx.run_with("core", Some(fx.scratch(&raised)), true);
        assert!(r
            .could_not_run
            .unwrap()
            .contains("the floor asserts 3 component(s)"));
    }

    #[test]
    fn image_equivalence_refuses_a_tree_without_the_flag_and_a_typoed_kind() {
        let fx = Fx::new();
        let r = fx.run_with("core", None, false);
        assert!(r.could_not_run.unwrap().contains("--allow-tree-only"));
        let typo = SSOT_T.replace("units = [", "unit = [");
        let r = fx.run_with("core", Some(fx.scratch(&typo)), true);
        assert!(r
            .could_not_run
            .unwrap()
            .contains(".unit is not a probe kind"));
    }

    #[test]
    fn image_equivalence_reports_a_recorded_profile_mismatch() {
        let fx = Fx::new();
        std::fs::create_dir_all(fx.dir.path().join("usr/lib/mios")).unwrap();
        std::fs::write(
            fx.dir.path().join("usr/lib/mios/version"),
            "MIOS_VERSION=0.3.0\nMIOS_PROFILES_DEFAULT=full\n",
        )
        .unwrap();
        let r = fx.run("core");
        assert!(r
            .findings
            .contains(&"image declares MIOS_PROFILES_DEFAULT=full (usr/lib/mios/version), caller asserts core".to_string()));
        assert!(fx.run("full").ok);
    }

    #[test]
    fn image_equivalence_rpm_probes_defer_only_under_tree_only() {
        let fx = Fx::new();
        let with_rpm = SSOT_T.replace(
            "commands = [\"podman\"]",
            "commands = [\"podman\"]\nrpm_sections = [\"base\"]",
        );
        std::fs::write(fx.dir.path().join(SSOT), &with_rpm).unwrap();
        let r = fx.run("core");
        assert!(
            r.ok && r.summary.contains("1 rpm probe(s) NOT run"),
            "{}",
            r.summary
        );
        let bad = with_rpm.replace(
            "rpm_sections = [\"base\"]",
            "rpm_sections = [\"zz-planted\"]",
        );
        std::fs::write(fx.dir.path().join(SSOT), bad).unwrap();
        assert!(fx.run("core").could_not_run.unwrap().contains("zz-planted"));
    }
}
