// AI-hint: Checks declared and tracked generator sources for non-portable fnmatch usage; unreadable inputs fail.
// AI-related: usr/share/mios/mios.toml, automation/98-drift-checks.sh

use crate::Report;
use std::collections::BTreeSet;
use std::path::{Component, Path};
use std::process::Command;

const CHECK: &str = "generator-host-parity";

fn inspect(root: &Path) -> Result<Report, String> {
    let policy = mios_resolver::resolve_projection(root).map_err(|e| e.to_string())?;
    let surfaces = policy
        .get("laws")
        .and_then(|v| v.get("projection_registry"))
        .and_then(|v| v.get("surfaces"))
        .and_then(|v| v.as_array())
        .filter(|v| !v.is_empty())
        .ok_or("SSOT projection registry surfaces are absent or empty")?;
    let mut sources = BTreeSet::new();
    for (index, surface) in surfaces.iter().enumerate() {
        let generator = surface
            .get("generator")
            .and_then(|v| v.as_str())
            .filter(|v| !v.trim().is_empty())
            .ok_or_else(|| format!("projection registry surface {index} has no generator"))?;
        if !Path::new(generator)
            .components()
            .all(|part| matches!(part, Component::Normal(_)))
        {
            return Err(format!(
                "generator path must stay inside the tree: {generator}"
            ));
        }
        sources.insert(generator.to_string());
    }
    if !root.join(".git").exists() {
        return Err("cannot enumerate tracked generators: .git is absent".to_string());
    }
    let listed = Command::new("git")
        .env("GIT_CEILING_DIRECTORIES", root.parent().unwrap_or(root))
        .arg("-C")
        .arg(root)
        .args(["ls-files", "-z", "--", "tools", "automation", "usr/libexec"])
        .output()
        .map_err(|e| format!("cannot enumerate tracked generators: {e}"))?;
    if !listed.status.success() {
        return Err(format!(
            "cannot enumerate tracked generators: {}",
            String::from_utf8_lossy(&listed.stderr).trim()
        ));
    }
    let listed = String::from_utf8(listed.stdout)
        .map_err(|e| format!("tracked generator paths are not UTF-8: {e}"))?;
    for path in listed.split('\0').filter(|path| !path.is_empty()) {
        let name = path.rsplit('/').next().unwrap_or(path);
        if name.starts_with("generate-")
            || name.starts_with("render-")
            || matches!(
                name,
                "mios-manual" | "mios-version-lint" | "mios_var_closure.py"
            )
        {
            sources.insert(path.to_string());
        }
    }
    let mut findings = Vec::new();
    for source in &sources {
        let text = std::fs::read_to_string(root.join(source))
            .map_err(|e| format!("cannot read generator {source}: {e}"))?;
        if text.contains("fnmatch.fnmatch(") {
            findings.push(format!(
                "{source} uses non-portable fnmatch.fnmatch instead of fnmatchcase"
            ));
        }
    }
    Ok(Report {
        check: CHECK.into(),
        ok: findings.is_empty(),
        could_not_run: None,
        summary: format!(
            "{} declared/discovered generator sources free of the non-portable fnmatch.fnmatch idiom",
            sources.len()
        ),
        findings,
    })
}

pub fn check(root: &Path) -> Report {
    inspect(root).unwrap_or_else(|error| Report {
        check: CHECK.into(),
        ok: false,
        could_not_run: Some(error),
        summary: String::new(),
        findings: Vec::new(),
    })
}

pub const FHS_CHECK: &str = "root-fhs-overlay";

pub const REQUIRED_FHS_DIRS: [&str; 18] = [
    "bin", "dev", "etc", "home", "lib", "lib64", "media", "mnt", "opt", "proc", "root", "run",
    "sbin", "srv", "sys", "tmp", "usr", "var",
];

pub const WINDOWS_HOST_NAMESPACES: [&str; 14] = [
    "Windows",
    "Program Files",
    "Program Files (x86)",
    "ProgramData",
    "Users",
    "$Recycle.Bin",
    "Recovery",
    "System Volume Information",
    "Riot Games",
    "XboxGames",
    "TEMP",
    "inetpub",
    "Documents and Settings",
    "PerfLogs",
];

pub const RUNTIME_WAYLAND_STACK: [&str; 3] = [
    "usr/share/wayland-sessions/hyprland.desktop",
    "usr/share/mios/sway/config",
    "usr/share/mios/quickshell",
];

pub const RUNTIME_NEURAL_PLANE: [&str; 5] = [
    "usr/lib/mios/agent-pipe/server.py",
    "usr/libexec/mios/gpu_arbiter.py",
    "usr/share/mios/llamacpp/mios-llm-light.yaml",
    "usr/share/containers/systemd/mios-llm-heavy.container",
    "usr/share/containers/systemd/mios-pgvector.container",
];

pub const RUNTIME_HARDWARE_STACK: [&str; 5] = [
    "usr/lib/udev/rules.d/99-kvmfr.rules",
    "usr/lib/systemd/system/system.slice.d/cpuset.conf",
    "usr/lib/systemd/zram-generator.conf.d/10-mios.conf",
    "usr/libexec/mios/launch-sovereign-hypervisor.sh",
    "usr/libexec/mios/usb-release.sh",
];

pub const BUILDTIME_STACK: [&str; 3] = [
    "Containerfile",
    "Containerfile.hummingbird",
    "Justfile",
];

pub const INSTALLATION_STACK: [&str; 4] = [
    "field/autounattend/MiOS-Provision.lib.ps1",
    "field/autounattend/New-MiOSAutounattend.ps1",
    "field/autounattend/New-MiOSISO.ps1",
    "field/autounattend/Build-MiOSXboxISO.ps1",
];

pub const PIPELINE_CI_STACK: [&str; 3] = [
    "tools/ci-suites.py",
    "tools/sync-generated.sh",
    "VERSION",
];

pub fn check_fhs_overlay(root: &Path) -> Report {
    let mut findings = Vec::new();

    // 1. Verify .git exists directly and is not a symlink/junction
    let git_dir = root.join(".git");
    if !git_dir.exists() {
        findings.push("root .git metadata directory is absent".to_string());
    } else if let Ok(meta) = std::fs::symlink_metadata(&git_dir) {
        if meta.file_type().is_symlink() {
            findings.push("root .git is a symlink or junction instead of a native root directory".to_string());
        }
    }

    // 2. Verify .archive bare store
    let archive_dir = root.join(".archive");
    if !archive_dir.is_dir() || !archive_dir.join("HEAD").exists() {
        findings.push("root .archive bare store is absent or invalid beside .git".to_string());
    }

    // 3. Verify all 18 Linux FHS root directories
    let mut verified_fhs = 0;
    for dir in REQUIRED_FHS_DIRS {
        let p = root.join(dir);
        if p.is_dir() {
            verified_fhs += 1;
        } else {
            findings.push(format!("missing standard Linux FHS root directory: /{dir}"));
        }
    }

    // 4. Verify Windows Host Namespaces isolation via .gitignore
    let gitignore = root.join(".gitignore");
    if !gitignore.is_file() {
        findings.push("root .gitignore is absent".to_string());
    } else if let Ok(content) = std::fs::read_to_string(&gitignore) {
        if !content.lines().any(|l| l.trim() == "/*") {
            findings.push("root .gitignore lacks mandatory '/*' block-all root boundary".to_string());
        }
        for ns in WINDOWS_HOST_NAMESPACES {
            let pat = format!("/{ns}");
            if !content.lines().any(|l| l.trim() == pat || l.trim() == ns) {
                findings.push(format!("root .gitignore does not explicitly isolate Windows host namespace: {ns}"));
            }
        }
    }

    // 5. Verify Platform Parity across Lifecycle Domains
    let mut verified_runtime = 0;
    for file in RUNTIME_WAYLAND_STACK {
        if root.join(file).exists() {
            verified_runtime += 1;
        } else {
            findings.push(format!("missing runtime Wayland desktop component: {file}"));
        }
    }
    for file in RUNTIME_NEURAL_PLANE {
        if root.join(file).exists() {
            verified_runtime += 1;
        } else {
            findings.push(format!("missing cognitive neural plane component: {file}"));
        }
    }
    for file in RUNTIME_HARDWARE_STACK {
        if root.join(file).exists() {
            verified_runtime += 1;
        } else {
            findings.push(format!("missing hardware/systemd integration component: {file}"));
        }
    }

    let mut verified_build = 0;
    for file in BUILDTIME_STACK {
        if root.join(file).exists() {
            verified_build += 1;
        } else {
            findings.push(format!("missing buildtime root component: {file}"));
        }
    }

    let mut verified_install = 0;
    for file in INSTALLATION_STACK {
        if root.join(file).exists() {
            verified_install += 1;
        } else {
            findings.push(format!("missing install-time autounattend/DISM servicing component: {file}"));
        }
    }

    let mut verified_pipeline = 0;
    for file in PIPELINE_CI_STACK {
        if root.join(file).exists() {
            verified_pipeline += 1;
        } else {
            findings.push(format!("missing pipeline/CI integrity component: {file}"));
        }
    }

    Report {
        check: FHS_CHECK.to_string(),
        ok: findings.is_empty(),
        could_not_run: None,
        summary: format!(
            "{verified_fhs}/18 FHS dirs, {verified_runtime} runtime, {verified_build} build, {verified_install} install, {verified_pipeline} pipeline components verified in native root overlay"
        ),
        findings,
    }
}

#[allow(dead_code)]
pub fn sync_fhs_overlay(root: &Path) -> std::io::Result<()> {
    for dir in REQUIRED_FHS_DIRS {
        let p = root.join(dir);
        if !p.exists() {
            std::fs::create_dir_all(&p)?;
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used, clippy::expect_used, clippy::panic)]
    use super::*;
    use std::fs;

    fn git(root: &Path, args: &[&str]) {
        let output = Command::new("git")
            .arg("-C")
            .arg(root)
            .args(args)
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
    }

    fn fixture() -> tempfile::TempDir {
        let tree = tempfile::tempdir().unwrap();
        git(tree.path(), &["init", "-q"]);
        git(tree.path(), &["config", "core.autocrlf", "false"]);
        fs::create_dir_all(tree.path().join("usr/share/mios")).unwrap();
        fs::create_dir_all(tree.path().join("tools/native/fixture/src")).unwrap();
        fs::write(
            tree.path().join("tools/native/fixture/src/main.rs"),
            "fn main() {}\n",
        )
        .unwrap();
        fs::write(
            tree.path().join("tools/render-fixture.py"),
            "print('fixture')\n",
        )
        .unwrap();
        fs::write(tree.path().join("usr/share/mios/mios.toml"),
            "[laws.projection_registry]\nsurfaces = [{ generator = 'tools/native/fixture/src/main.rs' }]\n").unwrap();
        git(tree.path(), &["add", "--", "tools/render-fixture.py"]);
        tree
    }

    #[test]
    fn native_and_legacy_sources_have_no_arbitrary_python_floor() {
        let tree = fixture();
        let report = check(tree.path());
        assert_eq!(report.code(), 0, "{}", report.render_text());
        assert!(report.summary.starts_with("2 declared/discovered"));
        fs::remove_file(tree.path().join("tools/render-fixture.py")).unwrap();
        git(
            tree.path(),
            &["rm", "--cached", "--", "tools/render-fixture.py"],
        );
        assert_eq!(check(tree.path()).code(), 0);
    }

    #[test]
    fn planted_idiom_is_named_and_source_bytes_are_preserved() {
        let tree = fixture();
        let path = tree.path().join("tools/render-fixture.py");
        let original = fs::read(&path).unwrap();
        let planted = b"import fnmatch\nfnmatch.fnmatch('x', 'x')\n";
        fs::write(&path, planted).unwrap();
        let report = check(tree.path());
        assert_eq!(report.code(), 1);
        assert!(report.findings[0].contains("tools/render-fixture.py"));
        assert_eq!(fs::read(&path).unwrap(), planted);
        fs::write(path, original).unwrap();
        assert_eq!(check(tree.path()).code(), 0);
    }

    #[test]
    fn listed_but_absent_or_invalid_utf8_sources_cannot_pass() {
        let tree = fixture();
        let path = tree.path().join("tools/render-fixture.py");
        fs::remove_file(&path).unwrap();
        let report = check(tree.path());
        assert_eq!(report.code(), 2);
        assert!(report
            .render_text()
            .contains("cannot read generator tools/render-fixture.py"));
        fs::write(path, [0xff]).unwrap();
        assert_eq!(check(tree.path()).code(), 2);
    }

    #[test]
    fn empty_registry_and_failed_git_enumeration_cannot_pass() {
        let tree = fixture();
        fs::write(
            tree.path().join("usr/share/mios/mios.toml"),
            "[laws.projection_registry]\nsurfaces = []\n",
        )
        .unwrap();
        let report = check(tree.path());
        assert_eq!(report.code(), 2);
        assert!(report.render_text().contains("absent or empty"));
        let tree = fixture();
        fs::remove_dir_all(tree.path().join(".git")).unwrap();
        let report = check(tree.path());
        assert_eq!(report.code(), 2);
        assert!(report
            .render_text()
            .contains("cannot enumerate tracked generators"));
    }

    #[test]
    fn layered_registry_requires_its_declared_sources() {
        let tree = fixture();
        fs::create_dir_all(tree.path().join("etc/mios")).unwrap();
        fs::write(
            tree.path().join("etc/mios/mios.toml"),
            "[laws.projection_registry]\nsurfaces = [{ generator = 'tools/native/missing.rs' }]\n",
        )
        .unwrap();
        let report = check(tree.path());
        assert_eq!(report.code(), 2);
        assert!(report.render_text().contains("tools/native/missing.rs"));
        fs::write(
            tree.path().join("tools/native/missing.rs"),
            "fn main() {}\n",
        )
        .unwrap();
        assert_eq!(check(tree.path()).code(), 0);
    }

    fn fhs_fixture() -> tempfile::TempDir {
        let tree = tempfile::tempdir().unwrap();
        let root = tree.path();
        // 1. .git directory
        fs::create_dir_all(root.join(".git")).unwrap();
        // 2. .archive bare store
        fs::create_dir_all(root.join(".archive")).unwrap();
        fs::write(root.join(".archive/HEAD"), "ref: refs/heads/legacy\n").unwrap();
        // 3. FHS dirs
        for dir in REQUIRED_FHS_DIRS {
            fs::create_dir_all(root.join(dir)).unwrap();
        }
        // 4. .gitignore with /* and namespaces
        let mut gitignore_content = String::from("/*\n");
        for ns in WINDOWS_HOST_NAMESPACES {
            gitignore_content.push_str(&format!("/{ns}\n"));
        }
        fs::write(root.join(".gitignore"), gitignore_content).unwrap();
        // 5. Lifecycle stacks
        for f in RUNTIME_WAYLAND_STACK
            .iter()
            .chain(RUNTIME_NEURAL_PLANE.iter())
            .chain(RUNTIME_HARDWARE_STACK.iter())
            .chain(BUILDTIME_STACK.iter())
            .chain(INSTALLATION_STACK.iter())
            .chain(PIPELINE_CI_STACK.iter())
        {
            let p = root.join(f);
            if let Some(parent) = p.parent() {
                fs::create_dir_all(parent).unwrap();
            }
            fs::write(p, "# stub\n").unwrap();
        }
        tree
    }

    #[test]
    fn fhs_overlay_positive_control_passes() {
        let tree = fhs_fixture();
        let report = check_fhs_overlay(tree.path());
        assert_eq!(report.code(), 0, "{}", report.render_text());
        assert!(report.ok);
        assert!(report.summary.contains("18/18 FHS dirs"));
    }

    #[test]
    fn fhs_overlay_planted_missing_git_fails() {
        let tree = fhs_fixture();
        fs::remove_dir(tree.path().join(".git")).unwrap();
        let report = check_fhs_overlay(tree.path());
        assert_eq!(report.code(), 1);
        assert!(report.findings.iter().any(|f| f.contains(".git metadata directory is absent")));
    }

    #[test]
    fn fhs_overlay_planted_missing_fhs_dir_fails() {
        let tree = fhs_fixture();
        fs::remove_dir(tree.path().join("dev")).unwrap();
        let report = check_fhs_overlay(tree.path());
        assert_eq!(report.code(), 1);
        assert!(report.findings.iter().any(|f| f.contains("missing standard Linux FHS root directory: /dev")));
    }

    #[test]
    fn fhs_overlay_planted_missing_namespace_fails() {
        let tree = fhs_fixture();
        fs::write(tree.path().join(".gitignore"), "/*\n").unwrap();
        let report = check_fhs_overlay(tree.path());
        assert_eq!(report.code(), 1);
        assert!(report.findings.iter().any(|f| f.contains("does not explicitly isolate Windows host namespace")));
    }

    #[test]
    fn fhs_overlay_planted_missing_component_fails() {
        let tree = fhs_fixture();
        fs::remove_file(tree.path().join("Containerfile")).unwrap();
        let report = check_fhs_overlay(tree.path());
        assert_eq!(report.code(), 1);
        assert!(report.findings.iter().any(|f| f.contains("missing buildtime root component: Containerfile")));
    }

    #[test]
    fn fhs_overlay_sync_creates_missing_dirs() {
        let tree = tempfile::tempdir().unwrap();
        assert!(!tree.path().join("proc").exists());
        sync_fhs_overlay(tree.path()).unwrap();
        for dir in REQUIRED_FHS_DIRS {
            assert!(tree.path().join(dir).is_dir());
        }
    }
}
