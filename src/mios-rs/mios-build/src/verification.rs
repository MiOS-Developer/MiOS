// AI-hint: SSOT-driven native toolchain and installed artifact verification shared by build, install and runtime callers; never certifies missing tools or foreign binaries.
// AI-related: usr/share/mios/mios.toml, automation/55-native-build.sh, src/mios-rs/miosd/src/main.rs

use object::read::pe::{ImageNtHeaders, ImageOptionalHeader, PeFile64};
use object::{endian::LittleEndian as LE, Object, ObjectSection};
use std::path::{Path, PathBuf};
use std::process::Command;

fn document(root: &Path) -> Result<toml::Value, String> {
    std::fs::read_to_string(root.join("usr/share/mios/mios.toml"))
        .map_err(|e| format!("cannot read native SSOT: {e}"))?
        .parse()
        .map_err(|e| format!("native SSOT TOML: {e}"))
}

fn config(root: &Path) -> Result<super::NativeConfig, String> {
    let text = std::fs::read_to_string(root.join("usr/share/mios/mios.toml"))
        .map_err(|e| format!("cannot read native SSOT: {e}"))?;
    super::native_config(&text)
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
                .current_dir(root),
            &format!("required {component} component for SSOT channel {channel}"),
        )?;
    }
    if lint {
        let config = super::native_config(
            &std::fs::read_to_string(root.join("usr/share/mios/mios.toml"))
                .map_err(|e| e.to_string())?,
        )?;
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
                || policy.target != "x86_64-pc-windows-gnu"
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
    let config = config(root)?;
    let policy = config.windows.ok_or("missing [build.native.windows]")?;
    if policy.target != "x86_64-pc-windows-gnu"
        || policy.linker != "x86_64-w64-mingw32-gcc"
        || policy.rustflags != ["-C", "target-feature=+crt-static"]
    {
        return Err("invalid SSOT Windows build policy".into());
    }
    let plan = super::native_target_plan(root, "windows")?;
    let selected = plan
        .iter()
        .find(|t| t.binary == binary)
        .ok_or_else(|| format!("{binary} is not a catalogued Windows executable"))?;
    toolchain_check(root, false)?;
    for subcommand in ["clippy", "build"] {
        let mut cmd = Command::new("cargo");
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
            .env("RUSTFLAGS", policy.rustflags.join(" "))
            .env("CARGO_TARGET_X86_64_PC_WINDOWS_GNU_LINKER", &policy.linker);
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
            if config.windows_only.iter().any(|v| v == binary) != (platform == "windows") {
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
        for name in &config.windows_only {
            std::fs::write(bins.join(format!("{name}.exe")), pe("kernel32.dll")).unwrap();
        }
        assert_eq!(
            runtime_check(root.path(), "windows", "x86_64", Some(&bins)).unwrap(),
            config.windows_only.len()
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
}
