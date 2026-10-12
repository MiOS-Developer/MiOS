// AI-hint: SSOT-driven native toolchain and installed artifact verification shared by build, install and runtime callers; never certifies missing tools or foreign binaries.
// AI-related: usr/share/mios/mios.toml, automation/55-native-build.sh, src/mios-rs/miosd/src/main.rs

use object::read::pe::{ImageNtHeaders, ImageOptionalHeader, PeFile64};
use object::{endian::LittleEndian as LE, Object, ObjectSection};
use std::path::{Path, PathBuf};
use std::process::Command;

fn document(root: &Path) -> Result<toml::Value, String> {
    super::native_document(root)
}

fn config(root: &Path) -> Result<super::NativeConfig, String> {
    super::resolved_native_config(root)
}

fn run(command: &mut Command, label: &str) -> Result<(), String> {
    let output = command.output().map_err(|e| format!("{label}: {e}"))?;
    if !output.status.success() {
        return Err(format!(
            "{label} failed: {}",
            String::from_utf8_lossy(&output.stderr)
        ));
    }
    Ok(())
}

/// Checks actual cargo subcommands, rather than treating the projected component
/// list as evidence of installation. Optional linting runs on the build host;
/// runtime verification never requires a compiler or a network download.
pub fn toolchain_check(root: &Path, lint: bool) -> Result<(), String> {
    let doc = document(root)?;
    let tc = doc
        .get("build")
        .and_then(|b| b.get("toolchain"))
        .ok_or("missing [build.toolchain]")?;
    let channel = tc
        .get("channel")
        .and_then(|v| v.as_str())
        .filter(|v| !v.trim().is_empty())
        .ok_or("missing [build.toolchain].channel")?;
    let components = tc
        .get("components")
        .and_then(|v| v.as_array())
        .ok_or("missing [build.toolchain].components")?;
    for (component, subcommand) in [("clippy", "clippy"), ("rustfmt", "fmt")] {
        if !components.iter().any(|v| v.as_str() == Some(component)) {
            return Err(format!(
                "[build.toolchain].components must include {component}"
            ));
        }
        run(
            Command::new("cargo")
                .args([subcommand, "--version"])
                .env("RUSTUP_TOOLCHAIN", channel)
                .current_dir(root),
            &format!("required {component} component for SSOT channel {channel}"),
        )?;
    }
    if lint {
        let config = config(root)?;
        // Resolve exclusions from Cargo inventory, not a copied binary roster.
        let platform = if cfg!(windows) { "windows" } else { "linux" };
        let excluded = super::native_target_plan(
            root,
            if platform == "linux" {
                "windows"
            } else {
                "linux"
            },
        )?;
        for workspace in &config.workspaces {
            let mut cmd = Command::new("cargo");
            cmd.args([
                "clippy",
                "--offline",
                "--locked",
                "--workspace",
                "--all-targets",
            ])
            .env("RUSTUP_TOOLCHAIN", channel)
            .current_dir(root.join(workspace));
            for target in excluded.iter().filter(|t| &t.workspace == workspace) {
                cmd.args(["--exclude", &target.package]);
            }
            cmd.args(["--", "-D", "warnings"]);
            run(&mut cmd, &format!("warning-fatal Clippy for {workspace}"))?;
        }
    }
    Ok(())
}

/// Windows uses PE and permitted OS DLL imports, not the Linux ELF predicate.
/// Refuse delay imports until they can be inspected; never hide that dependency.
pub fn verify_windows_pe(data: &[u8], allowed: &[String]) -> Result<(), String> {
    let file = PeFile64::parse(data).map_err(|e| format!("invalid PE64 executable: {e}"))?;
    let nt = file.nt_headers();
    let flags = nt.file_header().characteristics.get(LE);
    if nt.file_header().machine.get(LE) != object::pe::IMAGE_FILE_MACHINE_AMD64
        || flags & object::pe::IMAGE_FILE_EXECUTABLE_IMAGE == 0
        || flags & object::pe::IMAGE_FILE_DLL != 0
    {
        return Err("expected x86_64 PE executable, not a DLL or foreign machine".into());
    }
    if file
        .data_directory(object::pe::IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT)
        .is_some_and(|d| d.virtual_address.get(LE) != 0 || d.size.get(LE) != 0)
    {
        return Err("PE delay imports are not certified by the native verifier".into());
    }
    let entry = u64::from(nt.optional_header().address_of_entry_point());
    let entry_backed = file.sections().any(|s| {
        let object::SectionFlags::Coff { characteristics } = s.flags() else {
            return false;
        };
        let address = s.address().saturating_sub(file.relative_address_base());
        let length = s.data().map_or(0, |d| d.len() as u64);
        characteristics & object::pe::IMAGE_SCN_MEM_EXECUTE != 0
            && entry >= address
            && entry.checked_sub(address).is_some_and(|v| v < length)
    });
    if entry == 0 || !entry_backed {
        return Err("PE entry point is not backed by executable file data".into());
    }
    if let Some(table) = file
        .import_table()
        .map_err(|e| format!("invalid PE imports: {e}"))?
    {
        let mut descriptors = table.descriptors().map_err(|e| e.to_string())?;
        while let Some(desc) = descriptors
            .next()
            .map_err(|e| format!("invalid PE imports: {e}"))?
        {
            let name =
                std::str::from_utf8(table.name(desc.name.get(LE)).map_err(|e| e.to_string())?)
                    .map_err(|_| "non-UTF8 PE library name")?;
            if !allowed.iter().any(|v| v.eq_ignore_ascii_case(name)) {
                return Err(format!(
                    "PE dependency {name} is not an SSOT-approved system DLL"
                ));
            }
        }
    }
    Ok(())
}

pub fn artifact_check(root: &Path, path: &Path, platform: &str, arch: &str) -> Result<(), String> {
    let config = config(root)?;
    let data = std::fs::read(path).map_err(|e| format!("artifact {}: {e}", path.display()))?;
    match platform {
        "linux" => super::verify_static_elf(&data, arch, super::linux_target(&config, arch)?.pie),
        "windows" => {
            let policy = config.windows.ok_or("SSOT has no [build.native.windows]")?;
            if arch != "x86_64"
                || !matches!(
                    policy.target.as_str(),
                    "x86_64-pc-windows-gnu" | "x86_64-pc-windows-msvc"
                )
                || policy.rustflags != ["-C", "target-feature=+crt-static"]
                || policy.system_dlls.is_empty()
            {
                return Err(
                    "invalid SSOT Windows target, static-runtime flags or system DLL policy".into(),
                );
            }
            verify_windows_pe(&data, &policy.system_dlls)
        }
        _ => Err(format!("unsupported native platform {platform:?}")),
    }
}

/// Cross-build only a catalogued Windows executable, lint for that exact target,
/// inspect the resulting PE, then stage it. No guessed host-target fallback.
pub fn windows_build(root: &Path, binary: &str, output: &Path) -> Result<PathBuf, String> {
    let doc = document(root)?;
    let channel = doc
        .get("build")
        .and_then(|v| v.get("toolchain"))
        .and_then(|v| v.get("channel"))
        .and_then(toml::Value::as_str)
        .filter(|v| !v.is_empty())
        .ok_or("missing SSOT toolchain channel")?;
    let config = config(root)?;
    let policy = config.windows.ok_or("missing [build.native.windows]")?;
    let driver = windows_driver(&policy)?;
    if policy.rustflags != ["-C", "target-feature=+crt-static"] {
        return Err("invalid SSOT Windows build policy".into());
    }
    let plan = super::native_target_plan(root, "windows")?;
    let selected = plan
        .iter()
        .find(|t| t.binary == binary)
        .ok_or_else(|| format!("{binary} is not a catalogued Windows executable"))?;
    toolchain_check(root, false)?;
    run(
        Command::new("rustup").args(["target", "add", "--toolchain", channel, &policy.target]),
        "provision SSOT Windows target",
    )?;
    if driver == "xwin" {
        let version = policy
            .driver_version
            .as_deref()
            .ok_or("missing SSOT Windows driver_version")?;
        let installed = Command::new("cargo")
            .args(["xwin", "--version"])
            .env("RUSTUP_TOOLCHAIN", channel)
            .output();
        let matches = installed.is_ok_and(|out| {
            out.status.success()
                && String::from_utf8_lossy(&out.stdout)
                    .split_whitespace()
                    .last()
                    == Some(version)
        });
        if !matches {
            run(
                Command::new("cargo")
                    .args(["install", "--locked", "cargo-xwin", "--version", version])
                    .env("RUSTUP_TOOLCHAIN", channel),
                "provision SSOT Windows build driver",
            )?;
        }
    }
    for subcommand in ["clippy", "build"] {
        let mut cmd = Command::new("cargo");
        if driver == "xwin" {
            cmd.arg("xwin");
        }
        cmd.current_dir(root.join(&selected.workspace))
            .args([
                subcommand,
                "--locked",
                "--release",
                "--target",
                &policy.target,
                "-p",
                &selected.package,
                "--bin",
                binary,
                "--target-dir",
            ])
            .arg(output)
            .env("RUSTUP_TOOLCHAIN", channel)
            .env_remove("CARGO_ENCODED_RUSTFLAGS")
            .env("RUSTFLAGS", policy.rustflags.join(" "))
            .env(
                format!(
                    "CARGO_TARGET_{}_LINKER",
                    policy.target.to_uppercase().replace('-', "_")
                ),
                &policy.linker,
            );
        if subcommand == "clippy" {
            cmd.args(["--", "-D", "warnings"]);
        }
        run(&mut cmd, &format!("Windows {subcommand} for {binary}"))?;
    }
    let artifact = output
        .join(&policy.target)
        .join("release")
        .join(format!("{binary}.exe"));
    artifact_check(root, &artifact, "windows", "x86_64")?;
    let staged = root
        .join("tools/native/target")
        .join(&policy.target)
        .join("release")
        .join(format!("{binary}.exe"));
    if artifact != staged {
        std::fs::create_dir_all(staged.parent().ok_or("missing staging directory")?)
            .map_err(|e| e.to_string())?;
        std::fs::copy(&artifact, &staged).map_err(|e| e.to_string())?;
    }
    Ok(staged)
}

fn windows_driver(policy: &super::NativeWindows) -> Result<&'static str, String> {
    match (
        policy.target.as_str(),
        policy.driver.as_str(),
        policy.linker.as_str(),
    ) {
        ("x86_64-pc-windows-gnu", "cargo", "x86_64-w64-mingw32-gcc") => Ok("cargo"),
        ("x86_64-pc-windows-msvc", "cargo-xwin", "lld-link") => {
            let version = policy
                .driver_version
                .as_deref()
                .ok_or("missing SSOT Windows driver_version")?;
            if version.split('.').count() != 3
                || !version
                    .split('.')
                    .all(|part| !part.is_empty() && part.bytes().all(|b| b.is_ascii_digit()))
            {
                return Err("invalid SSOT Windows driver_version".into());
            }
            Ok("xwin")
        }
        _ => Err("invalid SSOT Windows build driver/target/linker combination".into()),
    }
}

/// Build the complete declared Windows catalog with the same lint and import
/// checks as an individual build. A failed member prevents installation.
pub fn windows_build_catalog(root: &Path, output: &Path) -> Result<usize, String> {
    let plan = super::native_target_plan(root, "windows")?;
    if plan.is_empty() {
        return Err("native Windows catalog selects zero artifacts".into());
    }
    for selected in &plan {
        windows_build(root, &selected.binary, output)?;
        println!("[native-build] verified Windows {}", selected.binary);
    }
    Ok(plan.len())
}

/// Uses the shipped role catalog without Cargo metadata, so the final image can
/// check its real installed programs after the build toolchain has been stripped.
/// Windows callers provide the installed flat bin directory; Linux uses FHS.
pub fn runtime_check(
    root: &Path,
    platform: &str,
    arch: &str,
    bin_dir: Option<&Path>,
) -> Result<usize, String> {
    if !matches!(platform, "linux" | "windows") {
        return Err(format!("unsupported native platform {platform:?}"));
    }
    if platform == "windows" && bin_dir.is_none() {
        return Err("Windows runtime verification requires --bin-dir".into());
    }
    let config = config(root)?;
    let mut count = 0;
    for category in config.categories.values() {
        for binary in &category.binaries {
            if !super::supports_platform(&config, binary, platform) {
                continue;
            }
            let directory = bin_dir
                .map(PathBuf::from)
                .unwrap_or_else(|| root.join(category.install_dir.trim_start_matches('/')));
            let path = directory.join(if platform == "windows" {
                format!("{binary}.exe")
            } else {
                binary.clone()
            });
            artifact_check(root, &path, platform, arch)
                .map_err(|e| format!("installed native {binary}: {e}"))?;
            count += 1;
        }
    }
    if count == 0 {
        return Err("native runtime catalog selects zero artifacts".into());
    }
    Ok(count)
}

/// Install the complete declared Windows catalog. Validate source and copied bytes
/// before replacement; retain recoverable originals if any replacement fails.
pub fn windows_install(root: &Path, source: &Path, destination: &Path) -> Result<usize, String> {
    let count = runtime_check(root, "windows", "x86_64", Some(source))?;
    let config = config(root)?;
    let mut names: Vec<String> = config
        .categories
        .values()
        .flat_map(|group| group.binaries.iter())
        .filter(|binary| super::supports_platform(&config, binary, "windows"))
        .map(|binary| format!("{binary}.exe"))
        .collect();
    names.sort();
    std::fs::create_dir_all(destination).map_err(|e| format!("Windows install directory: {e}"))?;
    let destination = destination.canonicalize().map_err(|e| e.to_string())?;
    if source.canonicalize().map_err(|e| e.to_string())? == destination {
        return Err("Windows staging and installation directories must differ".into());
    }
    for name in &names {
        match std::fs::symlink_metadata(destination.join(name)) {
            Ok(metadata) if !metadata.is_file() || metadata.file_type().is_symlink() => {
                return Err(format!(
                    "Windows install destination {name} is not a regular file"
                ))
            }
            Ok(_) => (),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => (),
            Err(e) => return Err(format!("Windows install destination {name}: {e}")),
        }
    }
    let transaction = tempfile::Builder::new()
        .prefix(".mios-native-install-")
        .tempdir_in(&destination)
        .map_err(|e| format!("Windows install staging: {e}"))?;
    let staged = transaction.path().join("new");
    let previous = transaction.path().join("previous");
    std::fs::create_dir(&staged)
        .and_then(|()| std::fs::create_dir(&previous))
        .map_err(|e| e.to_string())?;
    for name in &names {
        std::fs::copy(source.join(name), staged.join(name))
            .map_err(|e| format!("Windows stage {name}: {e}"))?;
    }
    runtime_check(root, "windows", "x86_64", Some(&staged))?;
    let mut replaced = Vec::new();
    let result = (|| -> Result<(), String> {
        for name in &names {
            let target = destination.join(name);
            let existed = target.exists();
            if existed {
                std::fs::rename(&target, previous.join(name))
                    .map_err(|e| format!("Windows preserve {name}: {e}"))?;
            }
            replaced.push((name, existed));
            std::fs::rename(staged.join(name), &target)
                .map_err(|e| format!("Windows install {name}: {e}"))?;
        }
        runtime_check(root, "windows", "x86_64", Some(&destination))?;
        Ok(())
    })();
    if let Err(error) = result {
        let mut errors = vec![error];
        for (name, existed) in replaced.into_iter().rev() {
            let target = destination.join(name);
            match std::fs::remove_file(&target) {
                Ok(()) => (),
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => (),
                Err(e) => {
                    errors.push(format!("rollback remove {name}: {e}"));
                    continue;
                }
            }
            if existed {
                if let Err(e) = std::fs::rename(previous.join(name), &target) {
                    errors.push(format!("rollback restore {name}: {e}"));
                }
            }
        }
        let recovery = transaction.keep();
        return Err(format!(
            "{}; preserved transaction: {}",
            errors.join("; "),
            recovery.display()
        ));
    }
    Ok(count)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pe(dll: &str) -> Vec<u8> {
        let mut data = vec![0; 1024];
        data[..2].copy_from_slice(b"MZ");
        data[60..64].copy_from_slice(&128_u32.to_le_bytes());
        data[128..132].copy_from_slice(b"PE\0\0");
        for (offset, value) in [
            (132, 0x8664_u16),
            (134, 1),
            (148, 240),
            (150, 0x22),
            (152, 0x20b),
        ] {
            data[offset..offset + 2].copy_from_slice(&value.to_le_bytes());
        }
        for (offset, value) in [
            (168, 0x1000_u32),
            (184, 0x1000),
            (188, 512),
            (208, 0x2000),
            (212, 512),
            (260, 16),
            (272, 0x1080),
            (276, 40),
            (400, 512),
            (404, 0x1000),
            (408, 512),
            (412, 512),
            (428, 0x60000020),
            (652, 0x10c0),
        ] {
            data[offset..offset + 4].copy_from_slice(&value.to_le_bytes());
        }
        data[176..184].copy_from_slice(&0x140000000_u64.to_le_bytes());
        data[392..397].copy_from_slice(b".text");
        data[512] = 0xc3;
        data[704..704 + dll.len()].copy_from_slice(dll.as_bytes());
        data
    }

    fn fixture() -> tempfile::TempDir {
        let root = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(root.path().join("usr/share/mios")).unwrap();
        std::fs::write(
            root.path().join("usr/share/mios/mios.toml"),
            include_str!("../../../../usr/share/mios/mios.toml"),
        )
        .unwrap();
        root
    }

    #[test]
    fn windows_build_driver_requires_a_compatible_target_and_pinned_version() {
        let root = fixture();
        let mut policy = config(root.path()).unwrap().windows.unwrap();
        assert_eq!(windows_driver(&policy).unwrap(), "xwin");
        for bad in ["", "...", "0.23", "0.23.1/../../", "--offline"] {
            policy.driver_version = Some(bad.into());
            assert!(windows_driver(&policy).is_err(), "accepted {bad:?}");
        }
        policy.driver_version = Some("0.23.1".into());
        policy.linker = "x86_64-w64-mingw32-gcc".into();
        assert!(windows_driver(&policy).is_err());
        policy.target = "x86_64-pc-windows-gnu".into();
        policy.driver = "cargo".into();
        assert_eq!(windows_driver(&policy).unwrap(), "cargo");
        policy.driver = "shell-script".into();
        assert!(windows_driver(&policy).is_err());
    }

    #[test]
    fn pe_accepts_system_import_and_rejects_compiler_dll() {
        let allowed = vec!["kernel32.dll".into()];
        verify_windows_pe(&pe("KERNEL32.dll"), &allowed).unwrap();
        let error = verify_windows_pe(&pe("libgcc_s_seh-1.dll"), &allowed).unwrap_err();
        assert!(error.contains("libgcc_s_seh-1.dll"), "{error}");
    }

    #[test]
    fn pe_rejects_foreign_truncated_delay_imports_and_unbacked_entry() {
        let allowed = vec!["kernel32.dll".into()];
        assert!(verify_windows_pe(b"MZfixture", &allowed)
            .unwrap_err()
            .contains("invalid PE"));
        let mut foreign = pe("kernel32.dll");
        foreign[132..134].copy_from_slice(&0xaa64_u16.to_le_bytes());
        assert!(verify_windows_pe(&foreign, &allowed)
            .unwrap_err()
            .contains("foreign machine"));
        let mut delay = pe("kernel32.dll");
        delay[368..372].copy_from_slice(&0x1080_u32.to_le_bytes());
        assert!(verify_windows_pe(&delay, &allowed)
            .unwrap_err()
            .contains("delay imports"));
        let mut entry = pe("kernel32.dll");
        entry[168..172].copy_from_slice(&0x5000_u32.to_le_bytes());
        assert!(verify_windows_pe(&entry, &allowed)
            .unwrap_err()
            .contains("entry point"));
        let mut bad_import = pe("kernel32.dll");
        bad_import[652..656].copy_from_slice(&0x5000_u32.to_le_bytes());
        assert!(verify_windows_pe(&bad_import, &allowed).is_err());
    }

    #[test]
    fn windows_runtime_checks_every_declared_artifact_without_cargo() {
        let root = fixture();
        let bins = root.path().join("windows bin with spaces");
        std::fs::create_dir(&bins).unwrap();
        let config = config(root.path()).unwrap();
        for name in config.windows_only.iter().chain(&config.windows_shared) {
            std::fs::write(bins.join(format!("{name}.exe")), pe("kernel32.dll")).unwrap();
        }
        assert_eq!(
            runtime_check(root.path(), "windows", "x86_64", Some(&bins)).unwrap(),
            config.windows_only.len() + config.windows_shared.len()
        );
        let target = bins.join(format!("{}.exe", config.windows_only[0]));
        std::fs::write(&target, pe("libgcc_s_seh-1.dll")).unwrap();
        assert!(runtime_check(root.path(), "windows", "x86_64", Some(&bins))
            .unwrap_err()
            .contains("libgcc"));
        std::fs::remove_file(target).unwrap();
        assert!(runtime_check(root.path(), "windows", "x86_64", Some(&bins))
            .unwrap_err()
            .contains("artifact"));
        assert!(runtime_check(root.path(), "windows", "x86_64", None).is_err());
        assert!(runtime_check(root.path(), "darwin", "aarch64", Some(&bins)).is_err());
    }

    #[test]
    fn windows_install_rejects_bad_catalog_before_replacing_existing_programs() {
        let root = fixture();
        let source = root.path().join("staged");
        let destination = root.path().join("installed");
        std::fs::create_dir(&source).unwrap();
        let config = config(root.path()).unwrap();
        for name in config.windows_only.iter().chain(&config.windows_shared) {
            std::fs::write(source.join(format!("{name}.exe")), pe("kernel32.dll")).unwrap();
        }
        assert_eq!(
            windows_install(root.path(), &source, &destination).unwrap(),
            config.windows_only.len() + config.windows_shared.len()
        );
        let original = std::fs::read(destination.join("mios-gen.exe")).unwrap();
        std::fs::write(source.join("mios-gen.exe"), pe("foreign-runtime.dll")).unwrap();
        assert!(windows_install(root.path(), &source, &destination)
            .unwrap_err()
            .contains("foreign-runtime"));
        assert_eq!(
            std::fs::read(destination.join("mios-gen.exe")).unwrap(),
            original
        );
        std::fs::write(source.join("mios-gen.exe"), pe("kernel32.dll")).unwrap();
        std::fs::remove_file(destination.join("mios-launch.exe")).unwrap();
        std::fs::create_dir(destination.join("mios-launch.exe")).unwrap();
        assert!(windows_install(root.path(), &source, &destination)
            .unwrap_err()
            .contains("not a regular file"));
        assert_eq!(
            std::fs::read(destination.join("mios-gen.exe")).unwrap(),
            original
        );
    }

    #[test]
    fn windows_install_replaces_catalog_and_preserves_unrelated_files() {
        let root = fixture();
        let source = root.path().join("staged");
        let destination = root.path().join("installed");
        std::fs::create_dir(&source).unwrap();
        std::fs::create_dir(&destination).unwrap();
        let config = config(root.path()).unwrap();
        let mut previous = pe("kernel32.dll");
        previous.extend_from_slice(b"previous generation");
        for name in config.windows_only.iter().chain(&config.windows_shared) {
            let name = format!("{name}.exe");
            std::fs::write(source.join(&name), pe("kernel32.dll")).unwrap();
            std::fs::write(destination.join(name), &previous).unwrap();
        }
        let unrelated = destination.join("operator-owned.txt");
        std::fs::write(&unrelated, b"preserve").unwrap();
        windows_install(root.path(), &source, &destination).unwrap();
        for name in config.windows_only.iter().chain(&config.windows_shared) {
            assert_eq!(
                std::fs::read(destination.join(format!("{name}.exe"))).unwrap(),
                pe("kernel32.dll")
            );
        }
        assert_eq!(std::fs::read(unrelated).unwrap(), b"preserve");
        assert!(windows_install(root.path(), &source, &source)
            .unwrap_err()
            .contains("must differ"));
    }

    #[cfg(windows)]
    #[test]
    fn windows_install_locked_program_restores_already_replaced_files() {
        use std::os::windows::fs::OpenOptionsExt;

        let root = fixture();
        let source = root.path().join("staged");
        let destination = root.path().join("installed");
        std::fs::create_dir(&source).unwrap();
        std::fs::create_dir(&destination).unwrap();
        let config = config(root.path()).unwrap();
        let mut previous = pe("kernel32.dll");
        previous.extend_from_slice(b"previous generation");
        let mut names: Vec<_> = config
            .windows_only
            .iter()
            .chain(&config.windows_shared)
            .map(|name| format!("{name}.exe"))
            .collect();
        names.sort();
        for name in &names {
            std::fs::write(source.join(name), pe("kernel32.dll")).unwrap();
            std::fs::write(destination.join(name), &previous).unwrap();
        }
        // Leave one earlier target absent, so rollback must also remove a new file.
        assert!(names[0].as_str() < "mios-gen.exe");
        std::fs::remove_file(destination.join(&names[0])).unwrap();
        let locked = std::fs::OpenOptions::new()
            .read(true)
            .share_mode(0)
            .open(destination.join("mios-gen.exe"))
            .unwrap();
        let error = windows_install(root.path(), &source, &destination).unwrap_err();
        assert!(error.contains("Windows preserve mios-gen.exe"), "{error}");
        assert!(error.contains("preserved transaction:"), "{error}");
        drop(locked);
        assert!(!destination.join(&names[0]).exists());
        for name in &names[1..] {
            assert_eq!(std::fs::read(destination.join(name)).unwrap(), previous);
        }
    }

    #[test]
    fn windows_policy_changes_are_consumed_by_artifact_check() {
        let root = fixture();
        let target = root.path().join("fixture.exe");
        std::fs::write(&target, pe("kernel32.dll")).unwrap();
        artifact_check(root.path(), &target, "windows", "x86_64").unwrap();
        let ssot = root.path().join("usr/share/mios/mios.toml");
        let original = std::fs::read_to_string(&ssot).unwrap();
        std::fs::write(
            &ssot,
            original.replace("\"kernel32.dll\"", "\"other-system.dll\""),
        )
        .unwrap();
        assert!(artifact_check(root.path(), &target, "windows", "x86_64")
            .unwrap_err()
            .contains("kernel32.dll"));
        assert!(artifact_check(root.path(), &target, "windows", "aarch64").is_err());
    }

    #[test]
    fn layered_host_policy_drives_build_and_artifact_verification() {
        let root = fixture();
        let host = root.path().join("etc/mios");
        std::fs::create_dir_all(&host).unwrap();
        let file = host.join("mios.toml");
        std::fs::write(&file, "[build.native.linux]\njobs=7\n").unwrap();
        assert_eq!(
            crate::native_linux_target(root.path(), "x86_64")
                .unwrap()
                .jobs,
            7
        );
        let artifact = root.path().join("fixture.exe");
        std::fs::write(&artifact, pe("kernel32.dll")).unwrap();
        artifact_check(root.path(), &artifact, "windows", "x86_64").unwrap();
        std::fs::write(
            &file,
            "[build.native.windows]\nsystem_dlls=['other-system.dll']\n",
        )
        .unwrap();
        assert!(artifact_check(root.path(), &artifact, "windows", "x86_64")
            .unwrap_err()
            .contains("kernel32.dll"));
        std::fs::write(&file, "[build.native.linux\n").unwrap();
        assert!(crate::native_linux_target(root.path(), "x86_64").is_err());
    }
}
