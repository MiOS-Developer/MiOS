// AI-hint: Build the pinned upstream GNU/NSS greeter and its carried account-display patch as an owned RPM, retaining locked corresponding source.
// AI-related: usr/share/mios/mios.toml [desktop.greeter.tuigreet_source], automation/65-bake-hyprland.sh, tools/patches/tuigreet-login-account.patch
// AI-functions: policy, spec, run, compiled_binary, audit, build_rpm, vendor_inventory
#![forbid(unsafe_code)]

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Component, Path, PathBuf};
use std::process::{Command, Stdio};
use toml::Value;

#[derive(Debug, Deserialize, Serialize)]
struct SourceFile {
    path: String,
    before_sha256: String,
    after_sha256: String,
}
#[derive(Debug, Deserialize, Serialize)]
pub struct Policy {
    repository: String,
    revision: String,
    version: String,
    archive_url: String,
    archive_sha256: String,
    archive_prefix: String,
    patch: String,
    patch_sha256: String,
    cargo_lock: String,
    cargo_lock_sha256: String,
    package_name: String,
    replaces_packages: Vec<String>,
    release: String,
    build_jobs: u32,
    expected_unit_tests: u32,
    focused_tests: Vec<String>,
    targets: BTreeMap<String, String>,
    interpreters: BTreeMap<String, String>,
    allowed_needed: Vec<String>,
    files: Vec<SourceFile>,
}
fn sha(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
fn require(ok: bool, message: &str) -> Result<(), String> {
    if ok {
        Ok(())
    } else {
        Err(message.into())
    }
}
fn hash(value: &str, length: usize) -> bool {
    value.len() == length && value.bytes().all(|b| b.is_ascii_hexdigit())
}
fn word(value: &str) -> bool {
    value
        .as_bytes()
        .first()
        .is_some_and(u8::is_ascii_alphanumeric)
        && value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b))
}
fn relative(value: &str) -> bool {
    !value.is_empty()
        && Path::new(value)
            .components()
            .all(|c| matches!(c, Component::Normal(_)))
}
fn text<'a>(doc: &'a Value, key: &str) -> Result<&'a str, String> {
    key.split('.')
        .try_fold(doc, |v, part| v.get(part))
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
        .ok_or_else(|| format!("missing SSOT {key}"))
}
pub fn policy(doc: &Value) -> Result<Policy, String> {
    let node = doc
        .get("desktop")
        .and_then(|v| v.get("greeter"))
        .and_then(|v| v.get("tuigreet_source"))
        .ok_or("missing desktop.greeter.tuigreet_source")?;
    let p: Policy = node
        .clone()
        .try_into()
        .map_err(|e| format!("invalid greeter source: {e}"))?;
    require(
        hash(&p.revision, 40)
            && hash(&p.archive_sha256, 64)
            && hash(&p.patch_sha256, 64)
            && hash(&p.cargo_lock_sha256, 64),
        "invalid greeter source seal",
    )?;
    require(
        [&p.version, &p.archive_prefix, &p.package_name, &p.release]
            .into_iter()
            .all(|v| word(v))
            && !p.replaces_packages.is_empty()
            && p.replaces_packages.iter().all(|v| word(v)),
        "invalid greeter RPM identity",
    )?;
    require(
        !p.replaces_packages.contains(&p.package_name),
        "patched greeter requires its own RPM name",
    )?;
    require(
        p.archive_prefix == format!("tuigreet-{}", p.revision),
        "upstream archive root must identify the pinned commit",
    )?;
    require(
        [&p.repository, &p.archive_url].into_iter().all(|v| {
            v.starts_with("https://")
                && v.bytes()
                    .all(|b| b.is_ascii_graphic() && !b"%\"'\\".contains(&b))
        }),
        "greeter source URLs must be plain HTTPS",
    )?;
    require(
        relative(&p.patch) && relative(&p.cargo_lock),
        "greeter inputs must stay inside the source root",
    )?;
    require(
        (1..=64).contains(&p.build_jobs)
            && p.expected_unit_tests > 0
            && !p.focused_tests.is_empty(),
        "invalid greeter build/test limits",
    )?;
    require(
        p.focused_tests.iter().all(|v| {
            !v.is_empty()
                && v.bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b"_:".contains(&b))
        }),
        "invalid exact greeter test name",
    )?;
    require(
        !p.files.is_empty()
            && p.files.iter().all(|f| {
                relative(&f.path) && hash(&f.before_sha256, 64) && hash(&f.after_sha256, 64)
            }),
        "invalid patched source file seals",
    )?;
    require(
        p.targets.iter().all(|(arch, target)| {
            word(arch) && word(target) && target.ends_with("-unknown-linux-gnu")
        }),
        "greeter must retain the GNU/NSS target",
    )?;
    require(
        p.interpreters.keys().eq(p.targets.keys())
            && p.interpreters.values().all(|path| {
                path.starts_with("/lib")
                    && path
                        .bytes()
                        .all(|b| b.is_ascii_alphanumeric() || b"/._-".contains(&b))
            })
            && p.allowed_needed.iter().any(|v| v == "libc.so.6")
            && p.allowed_needed.iter().all(|v| word(v)),
        "invalid GNU/NSS loader/dependency policy",
    )?;
    require(
        text(doc, "desktop.greeter.tuigreet")? == "/usr/bin/tuigreet"
            && text(doc, "terminal.remote_login.tuigreet")? == "/usr/bin/tuigreet",
        "both greeters must use the owned /usr/bin/tuigreet RPM path",
    )?;
    Ok(p)
}
fn command(program: &str, args: &[&str], cwd: &Path) -> Result<Vec<u8>, String> {
    let out = Command::new(program)
        .args(args)
        .current_dir(cwd)
        .env("LC_ALL", "C")
        .stdin(Stdio::null())
        .stderr(Stdio::inherit())
        .output()
        .map_err(|e| format!("{program}: {e}"))?;
    require(
        out.status.success(),
        &format!("{program} failed: {}", out.status),
    )?;
    Ok(out.stdout)
}
fn checked(path: &Path, expected: &str) -> Result<Vec<u8>, String> {
    let raw = fs::read(path).map_err(|e| format!("{}: {e}", path.display()))?;
    require(
        sha(&raw) == expected,
        &format!("source digest changed: {}", path.display()),
    )?;
    Ok(raw)
}
fn input(root: &Path, relative: &str, expected: &str) -> Result<PathBuf, String> {
    let path = root
        .join(relative)
        .canonicalize()
        .map_err(|e| e.to_string())?;
    require(
        path.starts_with(root) && path.is_file(),
        "greeter input escaped the source root",
    )?;
    checked(&path, expected)?;
    Ok(path)
}
fn source_files(p: &Policy, tree: &Path, after: bool) -> Result<(), String> {
    manifest_identity(p, tree)?;
    for file in &p.files {
        checked(
            &tree.join(&file.path),
            if after {
                &file.after_sha256
            } else {
                &file.before_sha256
            },
        )?;
    }
    checked(&tree.join("Cargo.lock"), &p.cargo_lock_sha256)?;
    Ok(())
}
fn manifest_identity(p: &Policy, tree: &Path) -> Result<(), String> {
    let manifest: Value = toml::from_str(
        &fs::read_to_string(tree.join("Cargo.toml"))
            .map_err(|e| format!("upstream Cargo.toml: {e}"))?,
    )
    .map_err(|e| e.to_string())?;
    let package = manifest
        .get("package")
        .ok_or("upstream Cargo.toml has no package")?;
    require(
        package.get("name").and_then(Value::as_str) == Some("tuigreet")
            && package.get("version").and_then(Value::as_str) == Some(p.version.as_str()),
        "upstream Cargo.toml package name/version differs from the SSOT RPM identity",
    )
}
fn extract(archive: &Path, tree: &Path, prefix: &str) -> Result<(), String> {
    let archive = archive.to_str().ok_or("archive path is not UTF-8")?;
    let listing = command("tar", &["-tzf", archive], tree)?;
    let listing = String::from_utf8(listing).map_err(|e| e.to_string())?;
    require(
        !listing.is_empty()
            && listing.lines().all(|line| {
                let path = Path::new(line);
                path.components().all(|c| matches!(c, Component::Normal(_)))
                    && path
                        .components()
                        .next()
                        .is_some_and(|v| v.as_os_str() == prefix)
            }),
        "upstream archive has an unexpected root or traversal",
    )?;
    let kinds =
        String::from_utf8(command("tar", &["-tvzf", archive], tree)?).map_err(|e| e.to_string())?;
    require(
        kinds
            .lines()
            .all(|line| line.starts_with('-') || line.starts_with('d')),
        "upstream archive contains links or special files",
    )?;
    command(
        "tar",
        &[
            "-xzf",
            archive,
            "--strip-components=1",
            "--no-same-owner",
            "--no-same-permissions",
        ],
        tree,
    )?;
    Ok(())
}
fn vendor_inventory(lock: &[u8], directory: &Path) -> Result<serde_json::Value, String> {
    let lock: Value = toml::from_str(std::str::from_utf8(lock).map_err(|e| e.to_string())?)
        .map_err(|e| e.to_string())?;
    let packages = lock
        .get("package")
        .and_then(Value::as_array)
        .ok_or("Cargo.lock has no package list")?;
    let mut expected = BTreeMap::new();
    for package in packages.iter().filter(|p| p.get("source").is_some()) {
        require(
            package["source"].as_str()
                == Some("registry+https://github.com/rust-lang/crates.io-index"),
            "unexpected locked dependency provider",
        )?;
        let name = package["name"]
            .as_str()
            .ok_or("locked dependency name missing")?;
        let version = package["version"]
            .as_str()
            .ok_or("locked dependency version missing")?;
        let checksum = package["checksum"]
            .as_str()
            .ok_or("locked dependency checksum missing")?;
        require(hash(checksum, 64), "invalid locked dependency checksum")?;
        expected.insert(format!("{name}-{version}"), checksum.to_owned());
    }
    let mut actual = BTreeMap::new();
    let mut files = BTreeMap::new();
    for entry in fs::read_dir(directory).map_err(|e| e.to_string())? {
        let entry = entry.map_err(|e| e.to_string())?;
        require(
            entry.file_type().map_err(|e| e.to_string())?.is_dir(),
            "vendor root must contain only crate directories",
        )?;
        let manifest: serde_json::Value = serde_json::from_slice(
            &fs::read(entry.path().join(".cargo-checksum.json")).map_err(|e| e.to_string())?,
        )
        .map_err(|e| e.to_string())?;
        actual.insert(
            entry.file_name().to_string_lossy().into_owned(),
            manifest["package"]
                .as_str()
                .ok_or("vendor crate checksum missing")?
                .to_owned(),
        );
        let declared: BTreeMap<String, String> = manifest["files"]
            .as_object()
            .ok_or("vendor file checksum map missing")?
            .iter()
            .map(|(name, value)| {
                let checksum = value
                    .as_str()
                    .ok_or("vendor file checksum must be a string")?;
                require(
                    relative(name) && hash(checksum, 64),
                    "invalid vendor file checksum entry",
                )?;
                Ok((name.clone(), checksum.to_owned()))
            })
            .collect::<Result<_, String>>()?;
        let mut observed = BTreeMap::new();
        for file in walkdir::WalkDir::new(entry.path()) {
            let file = file.map_err(|e| e.to_string())?;
            require(
                !file.file_type().is_symlink(),
                "vendor source links are not permitted",
            )?;
            if file.file_type().is_file() {
                let raw = fs::read(file.path()).map_err(|e| e.to_string())?;
                let digest = sha(&raw);
                let within_crate = file
                    .path()
                    .strip_prefix(entry.path())
                    .map_err(|e| e.to_string())?
                    .to_string_lossy()
                    .into_owned();
                if !matches!(within_crate.as_str(), ".cargo-checksum.json" | ".cargo-ok") {
                    observed.insert(within_crate, digest.clone());
                }
                files.insert(
                    file.path()
                        .strip_prefix(directory)
                        .map_err(|e| e.to_string())?
                        .to_string_lossy()
                        .into_owned(),
                    serde_json::json!({"sha256":digest,"bytes":raw.len()}),
                );
            }
        }
        require(
            observed == declared,
            "vendor source files differ from their complete checksum map",
        )?;
    }
    require(
        actual == expected,
        "vendored crates differ from the exact Cargo.lock provider/checksum set",
    )?;
    Ok(serde_json::json!({"locked_crates":actual,"files":files}))
}
fn sealed_vendor_cache(
    lock: &[u8],
    directory: &Path,
    seal: &str,
) -> Result<serde_json::Value, String> {
    require(
        hash(seal, 64),
        "offline vendor cache requires its reviewed inventory SHA-256",
    )?;
    let inventory = vendor_inventory(lock, directory)?;
    require(
        sha(&serde_json::to_vec(&inventory).map_err(|e| e.to_string())?) == seal,
        "offline vendor cache differs from its reviewed full-content inventory seal",
    )?;
    Ok(inventory)
}
fn same_vendor_sources(before: &serde_json::Value, after: &serde_json::Value) -> bool {
    let code = |inventory: &serde_json::Value| {
        inventory["files"].as_object().map(|files| {
            files
                .iter()
                .filter(|(name, _)| {
                    !name.split_once('/').is_some_and(|(_, within)| {
                        matches!(within, ".cargo-checksum.json" | ".cargo-ok")
                    })
                })
                .map(|(name, value)| (name.clone(), value.clone()))
                .collect::<BTreeMap<_, _>>()
        })
    };
    before["locked_crates"] == after["locked_crates"]
        && code(before).is_some()
        && code(before) == code(after)
}
fn needed_libraries(dynamic: &str) -> Result<BTreeSet<String>, String> {
    require(
        !dynamic.contains("(RPATH)") && !dynamic.contains("(RUNPATH)"),
        "greeter must not embed a library search path",
    )?;
    dynamic
        .lines()
        .filter(|line| line.contains("(NEEDED)"))
        .map(|line| {
            line.split_once("Shared library: [")
                .and_then(|(_, name)| name.strip_suffix(']'))
                .filter(|name| word(name))
                .map(str::to_owned)
                .ok_or("invalid readelf dependency record".into())
        })
        .collect()
}
fn compiled_binary(target: &str, top: &Path) -> Result<PathBuf, String> {
    let suffix = Path::new("target").join(target).join("release/tuigreet");
    let mut binaries = Vec::new();
    for entry in walkdir::WalkDir::new(top.join("BUILD")) {
        let entry = entry.map_err(|e| e.to_string())?;
        if entry.file_type().is_file() && entry.path().ends_with(&suffix) {
            binaries.push(entry.into_path());
        }
    }
    require(
        binaries.len() == 1,
        "RPM build must retain exactly one compiled greeter for the GNU audit",
    )?;
    Ok(binaries.remove(0))
}
fn audit(p: &Policy, target: &str, top: &Path) -> Result<serde_json::Value, String> {
    let binary = compiled_binary(target, top)?;
    let binary_path = binary.to_str().ok_or("compiled greeter path")?;
    let headers = command("readelf", &["-lW", binary_path], top)?;
    let dynamic = command("readelf", &["-dW", binary_path], top)?;
    let headers_text = std::str::from_utf8(&headers).map_err(|e| e.to_string())?;
    let interpreter = p
        .interpreters
        .get(std::env::consts::ARCH)
        .ok_or("GNU interpreter policy missing")?;
    require(
        headers_text.contains(&format!("Requesting program interpreter: {interpreter}]")),
        "greeter is not the selected GNU/NSS loader ABI",
    )?;
    let needed = needed_libraries(std::str::from_utf8(&dynamic).map_err(|e| e.to_string())?)?;
    require(
        needed.contains("libc.so.6") && needed.iter().all(|name| p.allowed_needed.contains(name)),
        "greeter has an unreviewed dynamic dependency",
    )?;
    Ok(
        serde_json::json!({"binary_sha256":sha(&fs::read(&binary).map_err(|e|e.to_string())?),"interpreter":interpreter,"needed":needed,"program_headers_sha256":sha(&headers),"dynamic_table_sha256":sha(&dynamic)}),
    )
}
fn build_rpm(top: &Path, spec_path: &Path) -> Result<(), String> {
    // RPM's final rmbuild stage removes BUILD on current releases. Retain this
    // invocation's private tree until the exact-one-binary GNU audit completes;
    // the owning TempDir still cleans it after outputs/provenance are copied.
    command(
        "rpmbuild",
        &[
            "-ba",
            "--noclean",
            "--define",
            &format!("_topdir {}", top.display()),
            spec_path.to_str().ok_or("spec path")?,
        ],
        top,
    )?;
    Ok(())
}
fn spec(
    p: &Policy,
    cargo: &str,
    rustup: &str,
    channel: &str,
    target: &str,
    vendor_sha: &str,
    inventory_sha: &str,
) -> String {
    let tests = p.focused_tests.join(" ");
    let provides = p
        .replaces_packages
        .iter()
        .map(|name| format!("Provides: {name} = %{{version}}-%{{release}}"))
        .collect::<Vec<_>>()
        .join("\n");
    let conflicts = p.replaces_packages.join(", ");
    format!(
        r#"Name: {name}
Version: {version}
Release: {release}
Summary: MiOS account-selecting NSS terminal greeter
License: GPL-3.0-only
URL: {repository}
{provides}
Conflicts: {conflicts}
Source0: upstream.tar.gz
Source1: vendor.tar.gz
Source2: Cargo.lock
Source3: source.json
Source4: vendor-inventory.json
Patch0: account-display.patch
%global debug_package %{{nil}}
%description
Upstream tuigreet with MiOS login names kept separate from pretty display names.
The distinct package name prevents a stock tuigreet update replacing this fix.
%prep
printf '%s\n' '{archive_sha} %{{SOURCE0}}' '{patch_sha} %{{PATCH0}}' '{lock_sha} %{{SOURCE2}}' '{vendor_sha} %{{SOURCE1}}' '{inventory_sha} %{{SOURCE4}}' | sha256sum -c -
%setup -q -n {prefix}
%patch -P 0 -p1
cmp Cargo.lock %{{SOURCE2}}
tar -xzf %{{SOURCE1}}
mkdir -p .cargo
printf '%s\n' '[source.crates-io]' 'replace-with = "vendored-sources"' '[source.vendored-sources]' 'directory = "vendor"' > .cargo/config.toml
%build
export RUSTUP_HOME={rustup} RUSTUP_TOOLCHAIN={channel} CARGO_HOME=%{{_builddir}}/mios-tuigreet-cargo
export CARGO_TARGET_DIR="$PWD/target"
export RUSTFLAGS='' CARGO_ENCODED_RUSTFLAGS=''
{cargo} build --release --locked --offline --target {target} --jobs {jobs}
%check
set -euo pipefail
export RUSTUP_HOME={rustup} RUSTUP_TOOLCHAIN={channel} CARGO_HOME=%{{_builddir}}/mios-tuigreet-cargo
export CARGO_TARGET_DIR="$PWD/target"
export RUSTFLAGS='' CARGO_ENCODED_RUSTFLAGS=''
{cargo} test --locked --offline --target {target} --jobs {jobs} -- --list > tests.list
test "$(grep -c ': test$' tests.list)" -eq {count}
for test_name in {tests}; do
    grep -Fx "$test_name: test" tests.list
    {cargo} test --locked --offline --target {target} --jobs {jobs} "$test_name" -- --exact
done
{cargo} test --locked --offline --target {target} --jobs {jobs}
target/{target}/release/tuigreet --version
target/{target}/release/tuigreet --help > /dev/null
%install
install -Dm0755 target/{target}/release/tuigreet %{{buildroot}}/usr/bin/tuigreet
install -Dm0644 %{{SOURCE3}} %{{buildroot}}%{{_datadir}}/mios/build/tuigreet-source.json
%files
/usr/bin/tuigreet
%license LICENSE
%{{_datadir}}/mios/build/tuigreet-source.json
"#,
        name = p.package_name,
        version = p.version,
        release = p.release,
        repository = p.repository,
        prefix = p.archive_prefix,
        jobs = p.build_jobs,
        count = p.expected_unit_tests,
        archive_sha = p.archive_sha256,
        patch_sha = p.patch_sha256,
        lock_sha = p.cargo_lock_sha256
    )
}
/// Produce private RPM/SRPM outputs; installation remains owned by the bake phase.
pub fn run(
    root: &Path,
    out: &Path,
    archive: Option<&Path>,
    vendor_cache: Option<&Path>,
    vendor_cache_sha256: Option<&str>,
) -> Result<(), String> {
    require(
        Path::new("/run/.containerenv").is_file(),
        "build-tuigreet requires the image builder or MiOS-DEV container",
    )?;
    let root = root.canonicalize().map_err(|e| e.to_string())?;
    let doc = mios_resolver::resolve_merged(Some(&root), false).map_err(|e| e.to_string())?;
    let p = policy(&doc)?;
    let target = p
        .targets
        .get(std::env::consts::ARCH)
        .ok_or("GNU greeter target is not configured for this architecture")?;
    let cargo = format!("{}/bin/cargo", text(&doc, "build.toolchain.cargo_home")?);
    let rustup = text(&doc, "build.toolchain.rustup_home")?;
    let channel = text(&doc, "build.toolchain.channel")?;
    require(
        [cargo.as_str(), rustup].into_iter().all(|v| {
            Path::new(v).is_absolute()
                && v.bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b"/._-".contains(&b))
        }) && word(channel),
        "unsafe configured Rust toolchain path/channel",
    )?;
    let patch = input(&root, &p.patch, &p.patch_sha256)?;
    let lock = input(&root, &p.cargo_lock, &p.cargo_lock_sha256)?;
    require(
        !out.exists()
            || !out.is_symlink()
                && out.is_dir()
                && fs::read_dir(out)
                    .map_err(|e| e.to_string())?
                    .next()
                    .is_none(),
        "RPM output directory must be fresh or empty without symlinks",
    )?;
    let work = tempfile::Builder::new()
        .prefix("mios-tuigreet-")
        .tempdir()
        .map_err(|e| e.to_string())?;
    let top = work.path();
    for name in [
        "SOURCES", "SPECS", "BUILD", "RPMS", "SRPMS", "tree", "cargo",
    ] {
        fs::create_dir(top.join(name)).map_err(|e| e.to_string())?;
    }
    let sources = top.join("SOURCES");
    let upstream = sources.join("upstream.tar.gz");
    if let Some(archive) = archive {
        fs::copy(archive, &upstream).map_err(|e| e.to_string())?;
    } else {
        command(
            "curl",
            &[
                "--fail",
                "--location",
                "--proto",
                "=https",
                "--proto-redir",
                "=https",
                "--output",
                upstream.to_str().ok_or("archive path")?,
                &p.archive_url,
            ],
            top,
        )?;
    }
    checked(&upstream, &p.archive_sha256)?;
    let tree = top.join("tree");
    extract(&upstream, &tree, &p.archive_prefix)?;
    source_files(&p, &tree, false)?;
    command(
        "git",
        &["apply", "--check", patch.to_str().ok_or("patch path")?],
        &tree,
    )?;
    command(
        "git",
        &["apply", patch.to_str().ok_or("patch path")?],
        &tree,
    )?;
    source_files(&p, &tree, true)?;
    let vendor = tree.join("vendor");
    let lock_bytes = checked(&lock, &p.cargo_lock_sha256)?;
    let cache = match (vendor_cache, vendor_cache_sha256) {
        (None, None) => None,
        (Some(path), Some(seal)) => {
            let path = path.canonicalize().map_err(|e| e.to_string())?;
            let inventory = sealed_vendor_cache(&lock_bytes, &path, seal)?;
            fs::create_dir_all(tree.join(".cargo")).map_err(|e| e.to_string())?;
            require(
                !tree.join(".cargo/config.toml").exists() && !tree.join(".cargo/config").exists(),
                "upstream source already declares Cargo source configuration",
            )?;
            let literal = Value::String(
                path.to_str()
                    .ok_or("vendor cache path must be UTF-8")?
                    .to_owned(),
            );
            fs::write(tree.join(".cargo/config.toml"),format!("[source.crates-io]\nreplace-with = \"reviewed-vendor-cache\"\n[source.reviewed-vendor-cache]\ndirectory = {literal}\n")).map_err(|e|e.to_string())?;
            Some((path, seal, inventory))
        }
        _ => {
            return Err(
                "offline vendor cache path and reviewed SHA-256 must be supplied together".into(),
            )
        }
    };
    let mut vendor_command = Command::new(&cargo);
    vendor_command.args(["vendor", "--locked", "--versioned-dirs"]);
    if cache.is_some() {
        vendor_command.args(["--respect-source-config", "--offline"]);
    }
    let output = vendor_command
        .arg("vendor")
        .current_dir(&tree)
        .env("RUSTUP_HOME", rustup)
        .env("RUSTUP_TOOLCHAIN", channel)
        .env("CARGO_HOME", top.join("cargo"))
        .env_remove("RUSTFLAGS")
        .env_remove("CARGO_ENCODED_RUSTFLAGS")
        .env_remove("CARGO_TARGET_DIR")
        .stdin(Stdio::null())
        .stderr(Stdio::inherit())
        .output()
        .map_err(|e| e.to_string())?;
    require(output.status.success(), "locked cargo vendor failed")?;
    source_files(&p, &tree, true)?;
    let inventory = vendor_inventory(&lock_bytes, &vendor)?;
    if let Some((path, seal, before)) = &cache {
        require(
            same_vendor_sources(before, &inventory),
            "cargo vendor changed or dropped reviewed cached source bytes",
        )?;
        require(
            sealed_vendor_cache(&lock_bytes, path, seal)? == *before,
            "reviewed vendor cache changed during preparation",
        )?;
    }
    let inventory_bytes = serde_json::to_vec(&inventory).map_err(|e| e.to_string())?;
    let version = Command::new(&cargo)
        .arg("--version")
        .env("RUSTUP_HOME", rustup)
        .env("RUSTUP_TOOLCHAIN", channel)
        .env("CARGO_HOME", top.join("cargo"))
        .output()
        .map_err(|e| e.to_string())?;
    require(
        version.status.success(),
        "configured Cargo version query failed",
    )?;
    let provenance = serde_json::json!({"schema":1,"policy":&p,"cargo_lock_sha256":p.cargo_lock_sha256,"vendor_inventory_sha256":sha(&inventory_bytes),"vendor_arguments":vendor_command.get_args().map(|a|a.to_string_lossy().into_owned()).collect::<Vec<_>>(),"vendor_cache":cache.as_ref().map(|(path,seal,_)|serde_json::json!({"path":path,"inventory_sha256":seal,"source_bytes_unchanged":true,"cargo_bookkeeping_wire_changes":[".cargo-checksum.json",".cargo-ok"]})),"target":target,"cargo_version":String::from_utf8_lossy(&version.stdout),"rustup_channel":channel,"linkage":"GNU/NSS external upstream; no native static exception"});
    fs::write(
        sources.join("source.json"),
        serde_json::to_vec_pretty(&provenance).map_err(|e| e.to_string())?,
    )
    .map_err(|e| e.to_string())?;
    fs::write(sources.join("vendor-inventory.json"), &inventory_bytes)
        .map_err(|e| e.to_string())?;
    command(
        "tar",
        &[
            "--sort=name",
            "--mtime=@0",
            "--owner=0",
            "--group=0",
            "--numeric-owner",
            "-czf",
            sources
                .join("vendor.tar.gz")
                .to_str()
                .ok_or("vendor archive path")?,
            "vendor",
        ],
        &tree,
    )?;
    let vendor_sha = sha(&fs::read(sources.join("vendor.tar.gz")).map_err(|e| e.to_string())?);
    fs::copy(&patch, sources.join("account-display.patch")).map_err(|e| e.to_string())?;
    fs::copy(&lock, sources.join("Cargo.lock")).map_err(|e| e.to_string())?;
    let spec_path = top.join("SPECS/mios-tuigreet.spec");
    fs::write(
        &spec_path,
        spec(
            &p,
            &cargo,
            rustup,
            channel,
            target,
            &vendor_sha,
            &sha(&inventory_bytes),
        ),
    )
    .map_err(|e| e.to_string())?;
    build_rpm(top, &spec_path)?;
    let elf = audit(&p, target, top)?;
    source_files(&p, &tree, true)?;
    fs::create_dir_all(out.join("source")).map_err(|e| e.to_string())?;
    let mut artifacts = Vec::new();
    for directory in ["RPMS", "SRPMS"] {
        for entry in walkdir::WalkDir::new(top.join(directory)) {
            let entry = entry.map_err(|e| e.to_string())?;
            if !entry.file_type().is_file() || entry.path().extension().is_none_or(|e| e != "rpm") {
                continue;
            }
            let source = directory == "SRPMS";
            let destination = if source {
                out.join("source")
            } else {
                out.to_path_buf()
            }
            .join(entry.file_name());
            let raw = fs::read(entry.path()).map_err(|e| e.to_string())?;
            fs::write(&destination, &raw).map_err(|e| e.to_string())?;
            artifacts.push(serde_json::json!({"path":destination,"source_rpm":source,"sha256":sha(&raw),"bytes":raw.len()}));
        }
    }
    require(
        artifacts
            .iter()
            .filter(|a| a["source_rpm"] == false)
            .count()
            == 1
            && artifacts.iter().filter(|a| a["source_rpm"] == true).count() == 1,
        "build must produce exactly one binary RPM and its matching source RPM",
    )?;
    fs::write(out.join("source/vendor-inventory.json"), &inventory_bytes)
        .map_err(|e| e.to_string())?;
    fs::write(out.join("build.json"), serde_json::to_vec_pretty(&serde_json::json!({"schema":1,"source":provenance,"artifacts":artifacts,"elf":elf,"installed":false})).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn document() -> Value {
        toml::from_str(include_str!("../../../../usr/share/mios/mios.toml")).unwrap()
    }
    #[test]
    fn package_owns_the_real_path_and_rejects_stock_replacement() {
        let p = policy(&document()).unwrap();
        let spec = spec(
            &p,
            "/toolchain/bin/cargo",
            "/toolchain/rustup",
            "stable",
            "x86_64-unknown-linux-gnu",
            &"a".repeat(64),
            &"b".repeat(64),
        );
        assert!(spec.contains("Name: mios-tuigreet\n"));
        assert!(spec.contains("Provides: tuigreet = %{version}-%{release}\n"));
        assert!(spec.contains("Provides: greetd-tuigreet = %{version}-%{release}\n"));
        assert!(spec.contains("Conflicts: tuigreet, greetd-tuigreet\n"));
        assert_eq!(
            spec.matches("export CARGO_TARGET_DIR=\"$PWD/target\"")
                .count(),
            2
        );
        assert!(spec.contains("%{buildroot}/usr/bin/tuigreet"));
        assert!(
            spec.contains("build --release --locked --offline --target x86_64-unknown-linux-gnu")
        );
        assert!(spec
            .contains("keyboard::test::editing_selected_login_removes_only_its_old_pretty_name"));
    }
    #[test]
    fn source_policy_refuses_unsafe_or_non_nss_builds() {
        let base = document();
        for (key, value) in [
            ("patch", "../escape.patch"),
            ("package_name", "tuigreet"),
            ("archive_url", "http://example.invalid/source.tar.gz"),
        ] {
            let mut doc = base.clone();
            doc["desktop"]["greeter"]["tuigreet_source"][key] = value.into();
            assert!(policy(&doc).is_err());
        }
        let mut doc = base;
        doc["desktop"]["greeter"]["tuigreet_source"]["targets"]["x86_64"] =
            "x86_64-unknown-linux-musl".into();
        assert!(policy(&doc).is_err());
    }
    #[test]
    fn external_elf_audit_preserves_needed_names_and_refuses_search_paths() {
        assert_eq!(needed_libraries("0x1 (NEEDED) Shared library: [libc.so.6]\n0x1 (NEEDED) Shared library: [libm.so.6]\n").unwrap(), BTreeSet::from(["libc.so.6".into(),"libm.so.6".into()]));
        assert!(needed_libraries("0x1 (RPATH) Library rpath: [/tmp/foreign]").is_err());
        assert!(needed_libraries("0x1 (RUNPATH) Library runpath: [/tmp/foreign]").is_err());
        assert!(needed_libraries("0x1 (NEEDED) malformed").is_err());
    }
    #[test]
    fn compiled_binary_requires_one_target_in_legacy_and_nested_rpm_layouts() {
        let target = "x86_64-unknown-linux-gnu";
        for layout in [
            "tuigreet-source",
            "mios-tuigreet-0.9.1-build/tuigreet-source",
        ] {
            let directory = tempfile::tempdir().unwrap();
            let top = directory.path();
            let build = top.join("BUILD");
            fs::create_dir(&build).unwrap();
            let other = build.join("other/target/aarch64-unknown-linux-gnu/release/tuigreet");
            fs::create_dir_all(other.parent().unwrap()).unwrap();
            fs::write(&other, b"another target").unwrap();
            assert!(compiled_binary(target, top)
                .unwrap_err()
                .contains("exactly one compiled greeter"));
            let binary = build
                .join(layout)
                .join("target")
                .join(target)
                .join("release/tuigreet");
            fs::create_dir_all(binary.parent().unwrap()).unwrap();
            fs::write(&binary, b"selected target").unwrap();
            assert_eq!(compiled_binary(target, top).unwrap(), binary);
            let duplicate = build
                .join("another-source/target")
                .join(target)
                .join("release/tuigreet");
            fs::create_dir_all(duplicate.parent().unwrap()).unwrap();
            fs::write(&duplicate, b"duplicate target").unwrap();
            assert!(compiled_binary(target, top)
                .unwrap_err()
                .contains("exactly one compiled greeter"));
            assert_eq!(fs::read(&binary).unwrap(), b"selected target");
            assert_eq!(fs::read(&duplicate).unwrap(), b"duplicate target");
            assert_eq!(fs::read(&other).unwrap(), b"another target");
        }
    }
    #[test]
    #[cfg(unix)]
    fn compiled_binary_refuses_file_and_directory_links() {
        let directory = tempfile::tempdir().unwrap();
        let top = directory.path();
        let outside = top.join("outside/target/x86_64-unknown-linux-gnu/release/tuigreet");
        fs::create_dir_all(outside.parent().unwrap()).unwrap();
        fs::write(&outside, b"linked target").unwrap();
        let link = top.join("BUILD/file/target/x86_64-unknown-linux-gnu/release/tuigreet");
        fs::create_dir_all(link.parent().unwrap()).unwrap();
        std::os::unix::fs::symlink(&outside, &link).unwrap();
        std::os::unix::fs::symlink(top.join("outside"), top.join("BUILD/directory")).unwrap();
        assert!(compiled_binary("x86_64-unknown-linux-gnu", top)
            .unwrap_err()
            .contains("exactly one compiled greeter"));
        assert!(fs::symlink_metadata(&link).unwrap().file_type().is_symlink());
        assert!(fs::symlink_metadata(top.join("BUILD/directory"))
            .unwrap()
            .file_type()
            .is_symlink());
        assert_eq!(fs::read(outside).unwrap(), b"linked target");
    }
    #[test]
    #[ignore = "requires Native MiOS-DEV with rpmbuild, cc and readelf"]
    fn rpm_completion_retains_binary_and_checks_until_gnu_audit() {
        assert!(
            Path::new("/run/.containerenv").is_file(),
            "run RPM retention validation inside the Native builder container"
        );
        let directory = tempfile::Builder::new()
            .prefix("mios-tuigreet-rpm-retention-")
            .tempdir()
            .unwrap();
        let top = directory.path();
        for name in ["SOURCES", "SPECS", "BUILD", "RPMS", "SRPMS"] {
            fs::create_dir(top.join(name)).unwrap();
        }
        let p = policy(&document()).unwrap();
        let target = p.targets.get(std::env::consts::ARCH).unwrap();
        let spec_path = top.join("SPECS/mios-greeter-audit-fixture.spec");
        // A real RPM lifecycle and GNU ELF, without downloading or rebuilding
        // the upstream dependency tree. The production build_rpm invocation
        // must retain BUILD and execute %check before the real ELF audit.
        fs::write(
            &spec_path,
            format!(
                r#"Name: mios-greeter-audit-fixture
Version: 1
Release: 1
Summary: Native RPM retention acceptance fixture
License: MIT
%global debug_package %{{nil}}
%description
Private fixture for the post-RPM GNU audit lifecycle.
%prep
%setup -q -T -c -n greeter-audit-fixture
%build
mkdir -p target/{target}/release
printf '%s\n' 'int main(void) {{ return 0; }}' > main.c
cc main.c -o target/{target}/release/tuigreet
%check
target/{target}/release/tuigreet
printf 'checked\n' > audit-check-ran
%install
install -Dm0755 target/{target}/release/tuigreet %{{buildroot}}/usr/libexec/mios-greeter-audit-fixture
%files
/usr/libexec/mios-greeter-audit-fixture
"#
            ),
        )
        .unwrap();
        let unrelated = top.join("unrelated-output");
        fs::write(&unrelated, b"preserve existing output").unwrap();
        build_rpm(top, &spec_path).unwrap();
        let result = audit(&p, target, top).unwrap();
        assert!(hash(result["binary_sha256"].as_str().unwrap(), 64));
        assert_eq!(result["interpreter"], p.interpreters[std::env::consts::ARCH]);
        let checks = walkdir::WalkDir::new(top.join("BUILD"))
            .into_iter()
            .map(Result::unwrap)
            .filter(|entry| entry.file_type().is_file() && entry.file_name() == "audit-check-ran")
            .collect::<Vec<_>>();
        assert_eq!(checks.len(), 1);
        assert_eq!(fs::read(checks[0].path()).unwrap(), b"checked\n");
        for name in ["RPMS", "SRPMS"] {
            let count = walkdir::WalkDir::new(top.join(name))
                .into_iter()
                .map(Result::unwrap)
                .filter(|entry| {
                    entry.file_type().is_file()
                        && entry.path().extension().is_some_and(|ext| ext == "rpm")
                })
                .count();
            assert_eq!(count, 1);
        }
        assert_eq!(fs::read(unrelated).unwrap(), b"preserve existing output");
    }
    #[test]
    fn vendor_catalogue_refuses_unlocked_crates_or_replaced_checksums() {
        let directory = tempfile::tempdir().unwrap();
        let crate_dir = directory.path().join("fixture-1.0.0");
        fs::create_dir(&crate_dir).unwrap();
        fs::write(crate_dir.join("lib.rs"), "// fixture\n").unwrap();
        let checksum = "a".repeat(64);
        let lock = format!("version = 3\n[[package]]\nname='fixture'\nversion='1.0.0'\nsource='registry+https://github.com/rust-lang/crates.io-index'\nchecksum='{checksum}'\n");
        let manifest = crate_dir.join(".cargo-checksum.json");
        let file_sha = sha(b"// fixture\n");
        fs::write(
            &manifest,
            serde_json::to_vec(
                &serde_json::json!({"package":checksum,"files":{"lib.rs":file_sha}}),
            )
            .unwrap(),
        )
        .unwrap();
        let inventory = vendor_inventory(lock.as_bytes(), directory.path()).unwrap();
        assert!(inventory["files"].get("fixture-1.0.0/lib.rs").is_some());
        fs::write(
            &manifest,
            serde_json::to_vec(
                &serde_json::json!({"package":"b".repeat(64),"files":{"lib.rs":file_sha}}),
            )
            .unwrap(),
        )
        .unwrap();
        assert!(vendor_inventory(lock.as_bytes(), directory.path()).is_err());
        fs::write(
            &manifest,
            serde_json::to_vec(
                &serde_json::json!({"package":checksum,"files":{"lib.rs":file_sha}}),
            )
            .unwrap(),
        )
        .unwrap();
        fs::create_dir(directory.path().join("unlocked-2.0.0")).unwrap();
        assert!(vendor_inventory(lock.as_bytes(), directory.path()).is_err());
    }
    #[test]
    fn rpm_version_cannot_mislabel_the_actual_upstream_manifest() {
        let tree = tempfile::tempdir().unwrap();
        fs::write(
            tree.path().join("Cargo.toml"),
            "[package]\nname='tuigreet'\nversion='0.9.1'\n",
        )
        .unwrap();
        let mut doc = document();
        assert!(manifest_identity(&policy(&doc).unwrap(), tree.path()).is_ok());
        doc["desktop"]["greeter"]["tuigreet_source"]["version"] = "0.9.2".into();
        assert!(manifest_identity(&policy(&doc).unwrap(), tree.path()).is_err());
        fs::write(
            tree.path().join("Cargo.toml"),
            "[package]\nname='another-program'\nversion='0.9.2'\n",
        )
        .unwrap();
        assert!(manifest_identity(&policy(&doc).unwrap(), tree.path()).is_err());
    }
    #[test]
    fn offline_vendor_input_binds_source_bytes_and_allows_only_bookkeeping_encoding() {
        let directory = tempfile::tempdir().unwrap();
        let crate_dir = directory.path().join("fixture-1.0.0");
        fs::create_dir(&crate_dir).unwrap();
        fs::write(crate_dir.join("lib.rs"), "// reviewed\n").unwrap();
        fs::create_dir(crate_dir.join("src")).unwrap();
        fs::write(crate_dir.join("src/.cargo-ok"), "real source asset").unwrap();
        let checksum = "a".repeat(64);
        let lock = format!("version=3\n[[package]]\nname='fixture'\nversion='1.0.0'\nsource='registry+https://github.com/rust-lang/crates.io-index'\nchecksum='{checksum}'\n");
        let manifest = serde_json::json!({"package":checksum,"files":{"lib.rs":sha(b"// reviewed\n"),"src/.cargo-ok":sha(b"real source asset")}});
        fs::write(
            crate_dir.join(".cargo-checksum.json"),
            serde_json::to_vec(&manifest).unwrap(),
        )
        .unwrap();
        let before = vendor_inventory(lock.as_bytes(), directory.path()).unwrap();
        let seal = sha(&serde_json::to_vec(&before).unwrap());
        assert!(sealed_vendor_cache(lock.as_bytes(), directory.path(), &seal).is_ok());
        assert!(sealed_vendor_cache(lock.as_bytes(), directory.path(), &"0".repeat(64)).is_err());
        fs::write(
            crate_dir.join(".cargo-checksum.json"),
            serde_json::to_vec_pretty(&manifest).unwrap(),
        )
        .unwrap();
        let metadata_only = vendor_inventory(lock.as_bytes(), directory.path()).unwrap();
        assert_ne!(before, metadata_only);
        assert!(same_vendor_sources(&before, &metadata_only));
        fs::write(crate_dir.join("src/.cargo-ok"), "changed source asset").unwrap();
        let nested_changed_manifest = serde_json::json!({"package":checksum,"files":{"lib.rs":sha(b"// reviewed\n"),"src/.cargo-ok":sha(b"changed source asset")}});
        fs::write(
            crate_dir.join(".cargo-checksum.json"),
            serde_json::to_vec(&nested_changed_manifest).unwrap(),
        )
        .unwrap();
        let nested_changed = vendor_inventory(lock.as_bytes(), directory.path()).unwrap();
        assert!(!same_vendor_sources(&before, &nested_changed));
        fs::write(crate_dir.join("src/.cargo-ok"), "real source asset").unwrap();
        fs::write(
            crate_dir.join(".cargo-checksum.json"),
            serde_json::to_vec(&manifest).unwrap(),
        )
        .unwrap();
        fs::write(crate_dir.join("lib.rs"), "// changed\n").unwrap();
        assert!(vendor_inventory(lock.as_bytes(), directory.path()).is_err());
        let changed_manifest = serde_json::json!({"package":checksum,"files":{"lib.rs":sha(b"// changed\n"),"src/.cargo-ok":sha(b"real source asset")}});
        fs::write(
            crate_dir.join(".cargo-checksum.json"),
            serde_json::to_vec(&changed_manifest).unwrap(),
        )
        .unwrap();
        let changed = vendor_inventory(lock.as_bytes(), directory.path()).unwrap();
        assert!(!same_vendor_sources(&before, &changed));
        assert!(sealed_vendor_cache(lock.as_bytes(), directory.path(), &seal).is_err());
    }
}
