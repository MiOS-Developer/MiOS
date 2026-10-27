// AI-hint: Static linkage gate for mios-gate: asserts no PT_INTERP or DT_NEEDED on Linux native binaries (Law 14, ADR-0021); --census adds the SHA-256 and dependency inventory.
// AI-related: src/mios-rs/mios-gate/src/main.rs, usr/share/mios/mios.toml, automation/98-drift-checks.sh, automation/55-native-build.sh

use crate::Report;
use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

const CHECK: &str = "static-linkage";

pub struct Options {
    pub root: PathBuf,
    pub binary: Option<PathBuf>,
    pub arch: Option<String>,
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

/// Detect ELF architecture (62 -> x86_64, 183 -> aarch64) from header.
fn detect_elf_arch(data: &[u8]) -> Option<&'static str> {
    if data.len() < 20 || data.get(..4) != Some(b"\x7fELF") {
        return None;
    }
    let machine = u16::from_le_bytes([data[18], data[19]]);
    match machine {
        62 => Some("x86_64"),
        183 => Some("aarch64"),
        _ => None,
    }
}

/// Resolve PIE requirement from SSOT or architecture defaults.
fn resolve_pie_policy(root: &Path, arch: &str) -> bool {
    let ssot_path = root.join("usr/share/mios/mios.toml");
    if ssot_path.is_file() {
        if let Ok(target_policy) = mios_build::native_linux_target(root, arch) {
            return target_policy.pie;
        }
        if let Ok(content) = std::fs::read_to_string(&ssot_path) {
            if let Ok(val) = content.parse::<toml::Value>() {
                if let Some(pie_val) = val
                    .get("build")
                    .and_then(|b| b.get("native"))
                    .and_then(|n| n.get("linux"))
                    .and_then(|l| l.get("pie"))
                    .and_then(|p| p.get(arch))
                    .and_then(|v| v.as_bool())
                {
                    return pie_val;
                }
            }
        }
    }
    // Architecture default: x86_64 requires static-pie, aarch64 currently does not
    arch == "x86_64"
}

/// One approved dynamic-linkage exception ([[build.native.linux.exceptions]]): upstream binaries
/// MiOS ships but does not build, exempt only inside `scope` (relative to the image root).
#[derive(Clone, Debug)]
struct Exception {
    binaries: BTreeSet<String>,
    scope: PathBuf,
    reason: String,
    allowed_needed: BTreeMap<String, BTreeSet<String>>,
}

/// What the SSOT says the gate audits beyond MiOS's own native workspaces: the exceptions, and the
/// upstream install trees ([build.native.linux].audit_scopes) that are scanned in full whether or
/// not an exception covers them, so removing an exception makes its binaries fail instead of
/// silently leaving the scan.
#[derive(Default)]
struct Policy {
    exceptions: Vec<Exception>,
    audit_scopes: Vec<PathBuf>,
    /// Malformed SSOT entries: each is a finding, so a broken exception can never exempt anything.
    errors: Vec<String>,
}

fn relative_scope(raw: &str) -> Option<PathBuf> {
    let p = PathBuf::from(raw.trim_end_matches('/'));
    let ok = !raw.is_empty()
        && p.is_relative()
        && p.components().all(|c| matches!(c, std::path::Component::Normal(_)));
    ok.then_some(p)
}

/// Load approved dynamic linkage exceptions and audit scopes from SSOT.
fn load_policy(root: &Path) -> Policy {
    let mut policy = Policy::default();
    let Some(linux) = std::fs::read_to_string(root.join("usr/share/mios/mios.toml"))
        .ok()
        .and_then(|c| c.parse::<toml::Value>().ok())
        .and_then(|v| v.get("build")?.get("native")?.get("linux").cloned())
    else {
        return policy;
    };
    for raw in linux.get("audit_scopes").and_then(|a| a.as_array()).into_iter().flatten() {
        match raw.as_str().and_then(relative_scope) {
            Some(p) => policy.audit_scopes.push(p),
            None => policy.errors.push(format!(
                "[build.native.linux].audit_scopes entry {raw} is not a relative path under the image root"
            )),
        }
    }
    for (i, item) in linux
        .get("exceptions")
        .and_then(|e| e.as_array())
        .into_iter()
        .flatten()
        .enumerate()
    {
        let mut binaries: BTreeSet<String> = item
            .get("binaries")
            .and_then(|b| b.as_array())
            .into_iter()
            .flatten()
            .filter_map(|b| b.as_str().map(str::to_string))
            .collect();
        if let Some(one) = item.get("binary").and_then(|b| b.as_str()) {
            binaries.insert(one.to_string());
        }
        let scope = item.get("scope").and_then(|s| s.as_str()).and_then(relative_scope);
        let reason = item
            .get("reason")
            .and_then(|r| r.as_str())
            .map(str::trim)
            .unwrap_or_default()
            .to_string();
        match scope {
            Some(scope) if !binaries.is_empty() && !reason.is_empty() => {
                if !policy.audit_scopes.contains(&scope) {
                    policy.errors.push(format!(
                        "[[build.native.linux.exceptions]] #{i} scope {} is not in [build.native.linux].audit_scopes, so nothing would audit it",
                        scope.display()
                    ));
                }
                let mut allowed_needed: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
                if let Some(an_val) = item.get("allowed_needed") {
                    if let Some(table) = an_val.as_table() {
                        for (bin_name, libs_val) in table {
                            if let Some(arr) = libs_val.as_array() {
                                let libs: BTreeSet<String> = arr
                                    .iter()
                                    .filter_map(|v| v.as_str().map(str::to_string))
                                    .collect();
                                if libs.len() != arr.len() {
                                    policy.errors.push(format!(
                                        "[[build.native.linux.exceptions]] #{i} allowed_needed.{bin_name} must contain only strings"
                                    ));
                                }
                                allowed_needed.insert(bin_name.clone(), libs);
                            } else {
                                policy.errors.push(format!(
                                    "[[build.native.linux.exceptions]] #{i} allowed_needed.{bin_name} must be an array of library sonames"
                                ));
                            }
                        }
                    } else {
                        policy.errors.push(format!(
                            "[[build.native.linux.exceptions]] #{i} allowed_needed must be a table"
                        ));
                    }
                }
                policy.exceptions.push(Exception {
                    binaries,
                    scope,
                    reason,
                    allowed_needed,
                });
            }
            _ => policy.errors.push(format!(
                "[[build.native.linux.exceptions]] #{i} needs binaries, a relative scope and a reason; it exempts nothing"
            )),
        }
    }
    policy
}

/// The exception that exempts `path`: its file name is listed AND it sits inside that exception's
/// scope -- under root/<scope>, or in any tree whose path ends in <scope> (a staged artifact).
fn exemption<'a>(path: &Path, root: &Path, policy: &'a Policy) -> Option<&'a Exception> {
    let name = path.file_name()?.to_str()?;
    policy.exceptions.iter().find(|e| {
        e.binaries.contains(name)
            && (path.starts_with(root.join(&e.scope))
                || path.ancestors().skip(1).any(|a| a.ends_with(&e.scope)))
    })
}

/// What the audit records about one ELF whatever the policy says of it: the
/// interpreter it asks for and the libraries it names. The verifier stops at
/// the first PT_INTERP or DT_NEEDED; this names all of them, so a finding says
/// WHICH loader and WHICH libraries a dynamic artifact pulled in.
#[derive(Default)]
struct ElfFacts {
    arch: String,
    kind: &'static str,
    interp: Option<String>,
    needed: Vec<String>,
}

fn le(data: &[u8], offset: usize, width: usize) -> Option<u64> {
    let bytes = data.get(offset..offset.checked_add(width)?)?;
    Some(
        bytes
            .iter()
            .enumerate()
            .fold(0, |n, (i, byte)| n | ((*byte as u64) << (i * 8))),
    )
}

fn cstr(data: &[u8], offset: usize) -> Option<String> {
    let tail = data.get(offset..)?;
    let end = tail.iter().position(|b| *b == 0).unwrap_or(tail.len());
    Some(String::from_utf8_lossy(tail.get(..end)?).into_owned())
}

/// Reads a little-endian ELF64's program headers. None for anything else, or
/// for a header table that does not fit the file.
fn elf_facts(data: &[u8]) -> Option<ElfFacts> {
    if data.get(..4) != Some(b"\x7fELF") || data.get(4) != Some(&2) || data.get(5) != Some(&1) {
        return None;
    }
    let machine = le(data, 18, 2)?;
    let mut facts = ElfFacts {
        arch: match machine {
            62 => "x86_64".to_string(),
            183 => "aarch64".to_string(),
            other => format!("machine_{other}"),
        },
        kind: match le(data, 16, 2)? {
            2 => "ET_EXEC",
            3 => "ET_DYN",
            _ => "other",
        },
        ..ElfFacts::default()
    };
    let phoff = usize::try_from(le(data, 32, 8)?).ok()?;
    let phentsize = usize::try_from(le(data, 54, 2)?).ok()?;
    let phnum = usize::try_from(le(data, 56, 2)?).ok()?;
    if phentsize < 56 {
        return None;
    }
    let mut loads: Vec<(u64, u64, u64)> = Vec::new(); // (vaddr, file offset, file size)
    let mut dynamic: Option<(usize, usize)> = None;
    for index in 0..phnum {
        let base = phoff.checked_add(index.checked_mul(phentsize)?)?;
        let offset = le(data, base + 8, 8)?;
        let filesz = le(data, base + 32, 8)?;
        match le(data, base, 4)? {
            1 => loads.push((le(data, base + 16, 8)?, offset, filesz)),
            2 => dynamic = Some((usize::try_from(offset).ok()?, usize::try_from(filesz).ok()?)),
            3 => facts.interp = cstr(data, usize::try_from(offset).ok()?),
            _ => {}
        }
    }
    if let Some((start, size)) = dynamic {
        let (mut needed, mut strtab) = (Vec::new(), None);
        for entry in (start..start.saturating_add(size)).step_by(16) {
            match le(data, entry, 8) {
                None | Some(0) => break,
                Some(1) => needed.push(le(data, entry + 8, 8)?),
                Some(5) => strtab = le(data, entry + 8, 8),
                Some(_) => {}
            }
        }
        // DT_STRTAB is an address; the PT_LOAD that maps it gives its file offset.
        let strtab = strtab.and_then(|addr| {
            loads
                .iter()
                .find(|(vaddr, _, filesz)| addr >= *vaddr && addr < vaddr.saturating_add(*filesz))
                .map(|(vaddr, offset, _)| offset + (addr - vaddr))
        });
        for name in needed {
            let resolved = strtab
                .and_then(|table| usize::try_from(table.checked_add(name)?).ok())
                .and_then(|at| cstr(data, at));
            facts
                .needed
                .push(resolved.unwrap_or_else(|| format!("strtab+{name}")));
        }
    }
    Some(facts)
}

/// The verifier's reason, plus which loader and libraries the artifact names.
fn describe_failure(error: &str, data: &[u8]) -> String {
    let Some(facts) = elf_facts(data) else {
        return error.to_string();
    };
    let mut out = error.to_string();
    if let Some(interp) = &facts.interp {
        out.push_str(&format!(" [interpreter: {interp}]"));
    }
    if !facts.needed.is_empty() {
        out.push_str(&format!(" [needs: {}]", facts.needed.join(", ")));
    }
    out
}

fn check_single_binary(
    path: &Path,
    arch_override: Option<&str>,
    root: &Path,
    policy: &Policy,
    findings: &mut Vec<String>,
) -> bool {
    if let Some(exc) = exemption(path, root, policy) {
        if !exc.allowed_needed.is_empty() {
            let data = match std::fs::read(path) {
                Ok(d) => d,
                Err(e) => {
                    findings.push(format!("{}: failed to read binary: {e}", path.display()));
                    return false;
                }
            };
            let Some(facts) = elf_facts(&data) else {
                findings.push(format!(
                    "{}: exempt binary is not a valid ELF64 file",
                    path.display()
                ));
                return false;
            };
            let bin_name = path
                .file_name()
                .and_then(|n| n.to_str())
                .unwrap_or_default();
            let Some(allowed) = exc.allowed_needed.get(bin_name) else {
                findings.push(format!(
                    "{}: exempt binary '{}' missing from exception allowed_needed table",
                    path.display(),
                    bin_name
                ));
                return false;
            };
            let unapproved: Vec<&str> = facts
                .needed
                .iter()
                .map(String::as_str)
                .filter(|lib| !allowed.contains(*lib))
                .collect();
            if !unapproved.is_empty() {
                findings.push(format!(
                    "{}: exempt dynamic binary links unapproved DT_NEEDED library(ies): {} [allowed: {}]",
                    path.display(),
                    unapproved.join(", "),
                    allowed.iter().cloned().collect::<Vec<_>>().join(", ")
                ));
                return false;
            }
        }
        return true;
    }

    let data = match std::fs::read(path) {
        Ok(d) => d,
        Err(e) => {
            findings.push(format!("{}: failed to read binary: {e}", path.display()));
            return false;
        }
    };

    let arch = match arch_override {
        Some(a) => a,
        None => detect_elf_arch(&data).unwrap_or("x86_64"),
    };

    let require_pie = resolve_pie_policy(root, arch);

    if let Err(e) = mios_build::verify_static_elf(&data, arch, require_pie) {
        findings.push(format!(
            "{}: {}",
            path.display(),
            describe_failure(&e, &data)
        ));
        return false;
    }
    true
}

fn is_elf_file(path: &Path) -> bool {
    if let Ok(mut f) = std::fs::File::open(path) {
        use std::io::Read;
        let mut magic = [0_u8; 4];
        if f.read_exact(&mut magic).is_ok() && &magic == b"\x7fELF" {
            return true;
        }
    }
    false
}

fn collect_elf_files_flat(dir: &Path, out: &mut Vec<PathBuf>) {
    let Ok(rd) = std::fs::read_dir(dir) else {
        return;
    };
    for entry in rd.flatten() {
        let path = entry.path();
        if path.is_file() && is_elf_file(&path) {
            out.push(path);
        }
    }
}

fn collect_elf_files_recursive(dir: &Path, out: &mut Vec<PathBuf>) {
    collect_elf_files_recursive_bounded(dir, out, 0);
}

fn collect_elf_files_recursive_bounded(dir: &Path, out: &mut Vec<PathBuf>, depth: usize) {
    if depth > 32 {
        return;
    }
    let Ok(rd) = std::fs::read_dir(dir) else {
        return;
    };
    for entry in rd.flatten() {
        let path = entry.path();
        if path.is_file() {
            if is_elf_file(&path) {
                out.push(path);
            }
        } else if path.is_dir() {
            collect_elf_files_recursive_bounded(&path, out, depth + 1);
        }
    }
}

pub fn check(opts: &Options) -> Report {
    let policy = load_policy(&opts.root);
    let mut findings = policy.errors.clone();

    if let Some(ref binary_path) = opts.binary {
        if !binary_path.is_file() {
            return cannot_run(format!(
                "specified binary {} does not exist",
                binary_path.display()
            ));
        }

        let ok = check_single_binary(
            binary_path,
            opts.arch.as_deref(),
            &opts.root,
            &policy,
            &mut findings,
        ) && policy.errors.is_empty();

        let exception = exemption(binary_path, &opts.root, &policy);
        let summary = if ok {
            if let Some(e) = exception {
                format!(
                    "binary {} approved dynamic exception ({}) under scope {}",
                    binary_path.display(),
                    e.reason,
                    e.scope.display()
                )
            } else {
                format!(
                    "binary {} verified as static ELF (no PT_INTERP, no DT_NEEDED)",
                    binary_path.display()
                )
            }
        } else {
            format!(
                "static linkage audit failed for binary {}: {} violation(s)",
                binary_path.display(),
                findings.len()
            )
        };

        return Report {
            check: CHECK.to_string(),
            ok,
            could_not_run: None,
            summary,
            findings,
        };
    }

    let binaries = match scanned(opts) {
        Ok(b) => b,
        // A malformed exception is a finding even when there is nothing to scan yet.
        Err(_) if !findings.is_empty() => {
            return Report {
                check: CHECK.to_string(),
                ok: false,
                could_not_run: None,
                summary: format!("static linkage policy is malformed: {} finding(s)", findings.len()),
                findings,
            }
        }
        Err(report) => return report,
    };

    for b in &binaries {
        check_single_binary(
            b,
            opts.arch.as_deref(),
            &opts.root,
            &policy,
            &mut findings,
        );
    }

    let ok = findings.is_empty();
    let summary = if ok {
        format!(
            "{} standalone Linux release binary(ies) verified as static ELF (no PT_INTERP, no DT_NEEDED)",
            binaries.len()
        )
    } else {
        format!(
            "static linkage audit failed: {} violation(s) detected across {} binary(ies)",
            findings.len(),
            binaries.len()
        )
    };
    Report {
        check: CHECK.to_string(),
        ok,
        could_not_run: None,
        summary,
        findings,
    }
}

/// Executable names built by the SSOT's native workspaces ([build.native].workspaces): each member's
/// package name plus any [[bin]] names. None when they cannot be derived (no SSOT or workspace in
/// the root), in which case the directory scan keeps every ELF file.
fn native_binary_names(root: &Path) -> Option<std::collections::HashSet<String>> {
    let ssot: toml::Value = std::fs::read_to_string(root.join("usr/share/mios/mios.toml")).ok()?.parse().ok()?;
    let workspaces = ssot.get("build")?.get("native")?.get("workspaces")?.as_array()?;
    let mut names = std::collections::HashSet::new();
    for ws in workspaces.iter().filter_map(|w| w.as_str()) {
        let ws_dir = root.join(ws);
        let Some(manifest) = std::fs::read_to_string(ws_dir.join("Cargo.toml"))
            .ok()
            .and_then(|t| t.parse::<toml::Value>().ok())
        else {
            continue;
        };
        let members = manifest
            .get("workspace")
            .and_then(|w| w.get("members"))
            .and_then(|m| m.as_array())
            .cloned()
            .unwrap_or_default();
        for member in members.iter().filter_map(|m| m.as_str()) {
            let Some(crate_toml) = std::fs::read_to_string(ws_dir.join(member).join("Cargo.toml"))
                .ok()
                .and_then(|t| t.parse::<toml::Value>().ok())
            else {
                continue;
            };
            if let Some(name) = crate_toml.get("package").and_then(|p| p.get("name")).and_then(|n| n.as_str()) {
                names.insert(name.to_string());
            }
            for bin in crate_toml.get("bin").and_then(|b| b.as_array()).into_iter().flatten() {
                if let Some(name) = bin.get("name").and_then(|n| n.as_str()) {
                    names.insert(name.to_string());
                }
            }
        }
    }
    (!names.is_empty()).then_some(names)
}

/// The standalone Linux release ELF binaries a directory scan audits.
fn scanned(opts: &Options) -> Result<Vec<PathBuf>, Report> {
    let mut binaries = Vec::new();

    // 1. Installed FHS directories in root -- only the binaries MiOS's own native workspaces build.
    // usr/bin also holds the distribution's executables (and on a host where the repo is checked out
    // at `/`, every installed package), which this policy does not govern.
    let native = native_binary_names(&opts.root);
    for sub in &["usr/bin", "usr/libexec/mios"] {
        let p = opts.root.join(sub);
        if p.is_dir() {
            let mut found = Vec::new();
            collect_elf_files_recursive(&p, &mut found);
            if let Some(names) = &native {
                found.retain(|f| {
                    f.file_name()
                        .and_then(|n| n.to_str())
                        .is_some_and(|n| names.contains(n))
                });
            }
            binaries.extend(found);
        }
    }

    // 2. Musl release target directories
    for sub in &[
        "tools/native/target/x86_64-unknown-linux-musl/release",
        "src/mios-rs/target/x86_64-unknown-linux-musl/release",
        "target/x86_64-unknown-linux-musl/release",
        "tools/native/target/aarch64-unknown-linux-musl/release",
        "src/mios-rs/target/aarch64-unknown-linux-musl/release",
        "target/aarch64-unknown-linux-musl/release",
    ] {
        let p = opts.root.join(sub);
        if p.is_dir() {
            collect_elf_files_recursive(&p, &mut binaries);
        }
    }

    // 3. Environment overrides
    if let Ok(dir) = std::env::var("MIOS_STATIC_LINKAGE_DIR") {
        let p = PathBuf::from(dir);
        if p.is_dir() {
            collect_elf_files_recursive(&p, &mut binaries);
        }
    }
    if let Ok(prefix) = std::env::var("MIOS_NATIVE_INSTALL_ROOT") {
        for sub in &["usr/bin", "usr/libexec/mios"] {
            let p = PathBuf::from(&prefix).join(sub);
            if p.is_dir() {
                collect_elf_files_recursive(&p, &mut binaries);
            }
        }
    }

    // 4. If root itself contains regular ELF files (e.g. test directory or flat output)
    if opts.root.is_dir() {
        let mut found = Vec::new();
        collect_elf_files_flat(&opts.root, &mut found);
        let bin_sub = opts.root.join("bin");
        if bin_sub.is_dir() {
            // On merged-/usr systems `bin` is a symlink to usr/bin: the same native-name filter applies.
            collect_elf_files_recursive(&bin_sub, &mut found);
        }
        if let Some(names) = &native {
            found.retain(|f| f.file_name().and_then(|n| n.to_str()).is_some_and(|n| names.contains(n)));
        }
        binaries.extend(found);
    }

    // 5. Upstream install trees named by the SSOT, in full and without the native-name filter:
    // every ELF there is either an exception inside its own scope or audited as MiOS code.
    for scope in &load_policy(&opts.root).audit_scopes {
        let p = opts.root.join(scope);
        if p.is_dir() {
            collect_elf_files_recursive(&p, &mut binaries);
        }
    }

    // Deduplicate binaries by canonical path
    binaries.sort();
    binaries.dedup();

    if binaries.is_empty() {
        return Err(cannot_run(format!(
            "no standalone Linux release ELF binaries found to audit in {}",
            opts.root.display()
        )));
    }
    Ok(binaries)
}

/// `static-linkage --census`: the gate's verdict plus an inventory of every
/// audited artifact -- SHA-256, size, architecture, ELF type, interpreter,
/// DT_NEEDED libraries, exemption and verdict -- and the dependency census
/// (library -> the binaries that need it). An OpenAI-format structured object
/// whose exit code is the gate's own, so a census is never a softer gate.
pub fn census(opts: &Options) -> (String, u8) {
    use sha2::Digest;

    let report = check(opts);
    let targets = match &opts.binary {
        Some(b) if b.is_file() => vec![b.clone()],
        Some(_) => Vec::new(),
        None => scanned(opts).unwrap_or_default(),
    };
    let policy = load_policy(&opts.root);
    let mut dependencies: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    let mut binaries = Vec::new();
    for path in &targets {
        let data = std::fs::read(path).unwrap_or_default();
        let name = path
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_default();
        let facts = elf_facts(&data).unwrap_or_default();
        let exception = exemption(path, &opts.root, &policy);
        let exempt = exception.is_some();
        let mut reasons = Vec::new();
        let passed = check_single_binary(
            path,
            opts.arch.as_deref(),
            &opts.root,
            &policy,
            &mut reasons,
        );
        for lib in &facts.needed {
            dependencies
                .entry(lib.clone())
                .or_default()
                .insert(name.clone());
        }
        let verdict = if exempt && passed {
            "exempt".to_string()
        } else if passed {
            "static".to_string()
        } else {
            reasons.join("; ")
        };
        binaries.push(serde_json::json!({
            "path": path.display().to_string(),
            "sha256": format!("{:x}", sha2::Sha256::digest(&data)),
            "size": data.len(),
            "arch": facts.arch,
            "type": facts.kind,
            "interpreter": facts.interp,
            "needed": facts.needed,
            "exempt": exempt,
            "exempt_scope": exception.map(|e| e.scope.display().to_string()),
            "exempt_reason": exception.map(|e| e.reason.clone()),
            "verdict": verdict,
        }));
    }
    let status = if report.could_not_run.is_some() {
        "could_not_run"
    } else if report.ok {
        "clean"
    } else {
        "violations"
    };
    let value = serde_json::json!({
        "check": CHECK,
        "status": status,
        "summary": report.could_not_run.clone().unwrap_or_else(|| report.summary.clone()),
        "findings": report.findings,
        "census": { "binaries": binaries, "dependencies": dependencies },
    });
    let text = serde_json::to_string_pretty(&value).unwrap_or_else(|_| {
        // Serialising our own object cannot fail, but a gate never panics.
        format!(
            "{{\"check\":\"{CHECK}\",\"status\":\"could_not_run\",\
             \"summary\":\"census could not be serialised\",\"findings\":[]}}"
        )
    });
    (text, report.code())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_elf(extra: u32) -> Vec<u8> {
        let mut data = vec![0_u8; 256];
        data[..7].copy_from_slice(b"\x7fELF\x02\x01\x01");
        data[16..18].copy_from_slice(&2_u16.to_le_bytes()); // ET_EXEC
        data[18..20].copy_from_slice(&62_u16.to_le_bytes()); // x86_64
        data[20..24].copy_from_slice(&1_u32.to_le_bytes());
        data[24..32].copy_from_slice(&0x1080_u64.to_le_bytes()); // entry
        data[32..40].copy_from_slice(&64_u64.to_le_bytes()); // phoff
        data[52..54].copy_from_slice(&64_u16.to_le_bytes()); // ehsize
        data[54..56].copy_from_slice(&56_u16.to_le_bytes()); // phentsize
        data[56..58].copy_from_slice(&2_u16.to_le_bytes()); // phnum
                                                            // Program header 0: PT_LOAD
        data[64..68].copy_from_slice(&1_u32.to_le_bytes()); // PT_LOAD
        data[68..72].copy_from_slice(&5_u32.to_le_bytes()); // PF_R | PF_X
        data[80..88].copy_from_slice(&0x1000_u64.to_le_bytes()); // vaddr
        data[96..104].copy_from_slice(&256_u64.to_le_bytes()); // filesz
        data[104..112].copy_from_slice(&256_u64.to_le_bytes()); // memsz
                                                                // Program header 1: extra
        data[120..124].copy_from_slice(&extra.to_le_bytes());
        if extra == 2 {
            // PT_DYNAMIC
            data[128..136].copy_from_slice(&224_u64.to_le_bytes()); // offset
            data[152..160].copy_from_slice(&32_u64.to_le_bytes()); // filesz
        }
        data
    }

    fn make_static_pie() -> Vec<u8> {
        let mut pie = make_elf(2);
        pie[16..18].copy_from_slice(&3_u16.to_le_bytes()); // ET_DYN
        pie[224..232].copy_from_slice(&0x6fff_fffb_u64.to_le_bytes()); // DT_FLAGS_1
        pie[232..240].copy_from_slice(&0x0800_0001_u64.to_le_bytes()); // DF_1_PIE
        pie
    }

    fn make_dynamic_interp() -> Vec<u8> {
        make_elf(3) // PT_INTERP
    }

    fn make_dynamic_needed() -> Vec<u8> {
        let mut dyn_bin = make_elf(2);
        dyn_bin[224..232].copy_from_slice(&1_u64.to_le_bytes()); // DT_NEEDED
        dyn_bin
    }

    /// A dynamic artifact whose DT_STRTAB resolves: it needs libfoo.so.
    fn make_needs_libfoo() -> Vec<u8> {
        let mut bin = make_elf(2);
        bin[224..232].copy_from_slice(&5_u64.to_le_bytes()); // DT_STRTAB
        bin[232..240].copy_from_slice(&(0x1000_u64 + 200).to_le_bytes()); // -> file offset 200
        bin[240..248].copy_from_slice(&1_u64.to_le_bytes()); // DT_NEEDED
        bin[248..256].copy_from_slice(&1_u64.to_le_bytes()); // strtab + 1
        bin[201..210].copy_from_slice(b"libfoo.so");
        bin
    }

    /// A dynamic artifact that asks for a named loader.
    fn make_interp_named() -> Vec<u8> {
        let mut bin = make_elf(3);
        bin[128..136].copy_from_slice(&200_u64.to_le_bytes()); // p_offset
        bin[152..160].copy_from_slice(&18_u64.to_le_bytes()); // p_filesz
        bin[200..217].copy_from_slice(b"/lib/ld-musl.so.1");
        bin
    }

    fn single(bytes: Vec<u8>) -> Report {
        let tmp = tempfile::tempdir().unwrap();
        let bin_path = tmp.path().join("dyn_bin");
        std::fs::write(&bin_path, bytes).unwrap();
        check(&Options {
            root: tmp.path().to_path_buf(),
            binary: Some(bin_path),
            arch: Some("x86_64".into()),
        })
    }

    #[test]
    fn a_dynamic_finding_names_the_libraries_it_needs() {
        let report = single(make_needs_libfoo());
        assert_eq!(report.code(), 1);
        assert!(
            report
                .findings
                .iter()
                .any(|f| f.contains("DT_NEEDED") && f.contains("[needs: libfoo.so]")),
            "{:?}",
            report.findings
        );
    }

    #[test]
    fn an_interpreter_finding_names_the_loader() {
        let report = single(make_interp_named());
        assert_eq!(report.code(), 1);
        assert!(
            report
                .findings
                .iter()
                .any(|f| f.contains("PT_INTERP") && f.contains("[interpreter: /lib/ld-musl.so.1]")),
            "{:?}",
            report.findings
        );
    }

    #[test]
    fn census_inventories_every_binary_and_keeps_the_gate_verdict() {
        use sha2::Digest;
        let tmp = tempfile::tempdir().unwrap();
        let (good, bad) = (make_static_pie(), make_needs_libfoo());
        std::fs::write(tmp.path().join("good_bin"), &good).unwrap();
        std::fs::write(tmp.path().join("dyn_bin"), &bad).unwrap();
        let (text, code) = census(&Options {
            root: tmp.path().to_path_buf(),
            binary: None,
            arch: Some("x86_64".into()),
        });
        assert_eq!(code, 1, "a census is never a softer gate: {text}");
        let v: serde_json::Value = serde_json::from_str(&text).unwrap();
        assert_eq!(v["status"], "violations");
        let bins = v["census"]["binaries"].as_array().unwrap();
        assert_eq!(bins.len(), 2, "{text}");
        let by_name = |n: &str| {
            bins.iter()
                .find(|b| b["path"].as_str().unwrap().ends_with(n))
                .unwrap()
                .clone()
        };
        let g = by_name("good_bin");
        assert_eq!(g["verdict"], "static");
        assert_eq!(g["sha256"], format!("{:x}", sha2::Sha256::digest(&good)));
        assert_eq!(g["arch"], "x86_64");
        assert_eq!(g["type"], "ET_DYN");
        let d = by_name("dyn_bin");
        assert_eq!(d["needed"], serde_json::json!(["libfoo.so"]));
        assert!(d["verdict"].as_str().unwrap().contains("DT_NEEDED"));
        assert_eq!(
            v["census"]["dependencies"]["libfoo.so"],
            serde_json::json!(["dyn_bin"])
        );
    }

    #[test]
    fn census_of_nothing_cannot_run() {
        let tmp = tempfile::tempdir().unwrap();
        let (text, code) = census(&Options {
            root: tmp.path().to_path_buf(),
            binary: None,
            arch: None,
        });
        assert_eq!(code, 2, "{text}");
        assert!(text.contains("could_not_run"), "{text}");
    }

    #[test]
    fn positive_control_static_pie_passes() {
        let tmp = tempfile::tempdir().unwrap();
        let bin_path = tmp.path().join("static_bin");
        std::fs::write(&bin_path, make_static_pie()).unwrap();

        let opts = Options {
            root: tmp.path().to_path_buf(),
            binary: Some(bin_path),
            arch: Some("x86_64".into()),
        };
        let report = check(&opts);
        assert!(
            report.ok,
            "Expected static PIE to pass: {:?}",
            report.findings
        );
        assert_eq!(report.code(), 0);
        assert!(report.findings.is_empty());
    }

    #[test]
    fn positive_control_directory_scan_passes() {
        let tmp = tempfile::tempdir().unwrap();
        let bin_path = tmp.path().join("my_service");
        std::fs::write(&bin_path, make_static_pie()).unwrap();

        let opts = Options {
            root: tmp.path().to_path_buf(),
            binary: None,
            arch: Some("x86_64".into()),
        };
        let report = check(&opts);
        assert!(
            report.ok,
            "Expected directory scan to pass: {:?}",
            report.findings
        );
        assert_eq!(report.code(), 0);
        assert!(report.summary.contains("1 standalone Linux release binary"));
    }

    #[test]
    fn negative_control_pt_interp_fails_and_names_defect() {
        let tmp = tempfile::tempdir().unwrap();
        let bin_path = tmp.path().join("dynamic_interp_bin");
        std::fs::write(&bin_path, make_dynamic_interp()).unwrap();

        let opts = Options {
            root: tmp.path().to_path_buf(),
            binary: Some(bin_path),
            arch: Some("x86_64".into()),
        };
        let report = check(&opts);
        assert!(!report.ok, "Expected dynamic binary with PT_INTERP to fail");
        assert_eq!(report.code(), 1);
        assert!(
            report.findings.iter().any(|f| f.contains("PT_INTERP")),
            "Expected defect to name PT_INTERP, got: {:?}",
            report.findings
        );
    }

    #[test]
    fn negative_control_dt_needed_fails_and_names_defect() {
        let tmp = tempfile::tempdir().unwrap();
        let bin_path = tmp.path().join("dynamic_needed_bin");
        std::fs::write(&bin_path, make_dynamic_needed()).unwrap();

        let opts = Options {
            root: tmp.path().to_path_buf(),
            binary: Some(bin_path),
            arch: Some("x86_64".into()),
        };
        let report = check(&opts);
        assert!(!report.ok, "Expected dynamic binary with DT_NEEDED to fail");
        assert_eq!(report.code(), 1);
        assert!(
            report.findings.iter().any(|f| f.contains("DT_NEEDED")),
            "Expected defect to name DT_NEEDED, got: {:?}",
            report.findings
        );
    }

    #[test]
    fn negative_control_non_pie_fails_on_x86_64() {
        let tmp = tempfile::tempdir().unwrap();
        let bin_path = tmp.path().join("non_pie_bin");
        std::fs::write(&bin_path, make_elf(0)).unwrap();

        let opts = Options {
            root: tmp.path().to_path_buf(),
            binary: Some(bin_path),
            arch: Some("x86_64".into()),
        };
        let report = check(&opts);
        assert!(!report.ok, "Expected non-PIE executable to fail on x86_64");
        assert_eq!(report.code(), 1);
        assert!(
            report.findings.iter().any(|f| f.contains("static-pie")),
            "Expected defect to name static-pie, got: {:?}",
            report.findings
        );
    }

    #[test]
    fn negative_control_corrupt_artifact_fails() {
        let tmp = tempfile::tempdir().unwrap();
        let bin_path = tmp.path().join("corrupt_bin");
        std::fs::write(&bin_path, b"MZ\x90\x00not_an_elf_binary").unwrap();

        let opts = Options {
            root: tmp.path().to_path_buf(),
            binary: Some(bin_path),
            arch: Some("x86_64".into()),
        };
        let report = check(&opts);
        assert!(!report.ok, "Expected non-ELF artifact to fail");
        assert_eq!(report.code(), 1);
    }

    #[test]
    fn missing_binary_cannot_run() {
        let tmp = tempfile::tempdir().unwrap();
        let bin_path = tmp.path().join("nonexistent_binary");

        let opts = Options {
            root: tmp.path().to_path_buf(),
            binary: Some(bin_path),
            arch: None,
        };
        let report = check(&opts);
        assert_eq!(report.code(), 2);
        assert!(report.could_not_run.is_some());
    }

    #[test]
    fn empty_directory_cannot_run() {
        let tmp = tempfile::tempdir().unwrap();

        let opts = Options {
            root: tmp.path().to_path_buf(),
            binary: None,
            arch: None,
        };
        let report = check(&opts);
        assert_eq!(report.code(), 2);
        assert!(report.could_not_run.is_some());
    }

    #[test]
    fn negative_control_directory_scan_violations_summary() {
        let tmp = tempfile::tempdir().unwrap();
        let bin_path = tmp.path().join("dynamic_service");
        std::fs::write(&bin_path, make_dynamic_interp()).unwrap();

        let opts = Options {
            root: tmp.path().to_path_buf(),
            binary: None,
            arch: Some("x86_64".into()),
        };
        let report = check(&opts);
        assert!(!report.ok, "Expected directory scan to fail");
        assert_eq!(report.code(), 1);
        assert!(!report.summary.contains("verified as static ELF"));
        assert!(report.summary.contains("static linkage audit failed"));
    }

    #[test]
    fn negative_control_single_binary_violations_summary() {
        let tmp = tempfile::tempdir().unwrap();
        let bin_path = tmp.path().join("dynamic_bin");
        std::fs::write(&bin_path, make_dynamic_interp()).unwrap();

        let opts = Options {
            root: tmp.path().to_path_buf(),
            binary: Some(bin_path),
            arch: Some("x86_64".into()),
        };
        let report = check(&opts);
        assert!(!report.ok);
        assert_eq!(report.code(), 1);
        assert!(!report.summary.contains("verified as static ELF"));
        assert!(report.summary.contains("static linkage audit failed"));
    }

    #[test]
    fn positive_control_recursive_directory_scan_finds_nested_binaries() {
        let tmp = tempfile::tempdir().unwrap();
        let nested_dir = tmp
            .path()
            .join("usr")
            .join("bin")
            .join("deep")
            .join("nested");
        std::fs::create_dir_all(&nested_dir).unwrap();
        let bin_path = nested_dir.join("deep_service");
        std::fs::write(&bin_path, make_static_pie()).unwrap();

        let opts = Options {
            root: tmp.path().to_path_buf(),
            binary: None,
            arch: Some("x86_64".into()),
        };
        let report = check(&opts);
        assert!(
            report.ok,
            "Expected nested directory scan to pass: {:?}",
            report.findings
        );
        assert_eq!(report.code(), 0);
        assert!(report.summary.contains("1 standalone Linux release binary"));
    }

    /// A root with an SSOT that audits usr/lib/mios/portal and (optionally) exempts Gecko there.
    fn gecko_root(exception: Option<&str>) -> tempfile::TempDir {
        let tmp = tempfile::tempdir().unwrap();
        let ssot = tmp.path().join("usr/share/mios");
        std::fs::create_dir_all(&ssot).unwrap();
        let mut text = String::from("[build.native.linux]\naudit_scopes = [\"usr/lib/mios/portal\"]\n");
        if let Some(ex) = exception {
            text.push_str(ex);
        }
        std::fs::write(ssot.join("mios.toml"), text).unwrap();
        let portal = tmp.path().join("usr/lib/mios/portal");
        std::fs::create_dir_all(portal.join("gmp-clearkey/0.1")).unwrap();
        for name in ["mios-portal", "libxul.so", "plugin-container"] {
            std::fs::write(portal.join(name), make_needs_libfoo()).unwrap();
        }
        std::fs::write(portal.join("gmp-clearkey/0.1/libclearkey.so"), make_dynamic_needed()).unwrap();
        tmp
    }

    const GECKO: &str = "[[build.native.linux.exceptions]]\nscope = \"usr/lib/mios/portal\"\nreason = \"upstream Gecko\"\nbinaries = [\"mios-portal\", \"libxul.so\", \"plugin-container\", \"libclearkey.so\"]\n";

    fn opts(root: &Path) -> Options {
        Options { root: root.to_path_buf(), binary: None, arch: Some("x86_64".into()) }
    }

    #[test]
    fn positive_control_census_lists_gecko_binaries_in_scope_as_exempt() {
        let tmp = gecko_root(Some(GECKO));
        let (text, code) = census(&opts(tmp.path()));
        assert_eq!(code, 0, "{text}");
        let v: serde_json::Value = serde_json::from_str(&text).unwrap();
        let bins = v["census"]["binaries"].as_array().unwrap();
        assert_eq!(bins.len(), 4, "{text}");
        for b in bins {
            assert_eq!(b["exempt"], true, "{b}");
            assert_eq!(b["verdict"], "exempt");
            assert_eq!(b["exempt_scope"], "usr/lib/mios/portal");
            assert_eq!(b["exempt_reason"], "upstream Gecko");
        }
    }

    #[test]
    fn negative_control_an_unlisted_dynamic_elf_in_scope_fails() {
        let tmp = gecko_root(Some(GECKO));
        std::fs::write(tmp.path().join("usr/lib/mios/portal/libsneaky.so"), make_needs_libfoo()).unwrap();
        let report = check(&opts(tmp.path()));
        assert_eq!(report.code(), 1);
        assert!(report.findings.iter().any(|f| f.contains("libsneaky.so")), "{:?}", report.findings);
        assert_eq!(report.findings.len(), 1, "only the unlisted one: {:?}", report.findings);
    }

    #[test]
    fn negative_control_removing_the_exception_fails_the_binaries_in_scope() {
        let tmp = gecko_root(None);
        let report = check(&opts(tmp.path()));
        assert_eq!(report.code(), 1, "{:?}", report.findings);
        assert_eq!(report.findings.len(), 4, "{:?}", report.findings);
        assert!(report.findings.iter().any(|f| f.contains("libxul.so")));
    }

    #[test]
    fn negative_control_a_listed_name_outside_its_scope_is_not_exempt() {
        let tmp = gecko_root(Some(GECKO));
        let elsewhere = tmp.path().join("libxul.so");
        std::fs::write(&elsewhere, make_needs_libfoo()).unwrap();
        let report = check(&Options { binary: Some(elsewhere), ..opts(tmp.path()) });
        assert_eq!(report.code(), 1, "{:?}", report.findings);
        // The same name inside a staged copy of the scope is exempt.
        let staged = tmp.path().join("stage/usr/lib/mios/portal");
        std::fs::create_dir_all(&staged).unwrap();
        std::fs::write(staged.join("libxul.so"), make_needs_libfoo()).unwrap();
        let report = check(&Options { binary: Some(staged.join("libxul.so")), ..opts(tmp.path()) });
        assert_eq!(report.code(), 0, "{:?}", report.findings);
    }

    #[test]
    fn negative_control_an_exception_without_a_reason_exempts_nothing() {
        let tmp = gecko_root(Some(&GECKO.replace("reason = \"upstream Gecko\"\n", "")));
        let report = check(&opts(tmp.path()));
        assert_eq!(report.code(), 1);
        assert!(report.findings.iter().any(|f| f.contains("needs binaries, a relative scope and a reason")));
        let tmp = gecko_root(Some(&GECKO.replace("usr/lib/mios/portal", "/abs/portal")));
        assert_eq!(check(&opts(tmp.path())).code(), 1);
    }

    #[test]
    fn the_shipped_ssot_exempts_gecko_only_inside_the_portal_install_dir() {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..");
        let policy = load_policy(&root);
        assert!(policy.errors.is_empty(), "{:?}", policy.errors);
        let gecko = policy
            .exceptions
            .iter()
            .find(|e| e.binaries.contains("libxul.so"))
            .expect("[[build.native.linux.exceptions]] names libxul.so");
        // Executables the Firefox 158 artifact actually ships (it has no plugin-container).
        for name in ["mios-portal", "firefox-bin", "pingsender"] {
            assert!(gecko.binaries.contains(name), "{name} is not exempt");
        }
        assert_eq!(gecko.scope, PathBuf::from("usr/lib/mios/portal"));
        assert!(gecko.reason.len() > 20);
        assert!(policy.audit_scopes.contains(&gecko.scope));
    }

    #[test]
    fn positive_control_single_binary_approved_exception_succeeds() {
        let tmp = gecko_root(Some(GECKO));
        let portal = tmp.path().join("usr/lib/mios/portal/mios-portal");
        let report = check(&Options { binary: Some(portal), ..opts(tmp.path()) });
        assert_eq!(report.code(), 0, "{:?}", report.findings);
        assert!(report.summary.contains("approved dynamic exception"), "{:?}", report.summary);
    }

    #[test]
    fn allowed_needed_enforces_exact_dt_needed_subset_on_exempt_binaries() {
        let ex_ok = format!(
            "{GECKO}[build.native.linux.exceptions.allowed_needed]\n\"mios-portal\" = [\"libfoo.so\"]\n\"libxul.so\" = [\"libfoo.so\"]\n\"plugin-container\" = [\"libfoo.so\"]\n\"libclearkey.so\" = [\"strtab+0\"]\n"
        );
        let tmp_ok = gecko_root(Some(&ex_ok));
        let report_ok = check(&opts(tmp_ok.path()));
        assert_eq!(report_ok.code(), 0, "{:?}", report_ok.findings);

        let ex_bad = format!(
            "{GECKO}[build.native.linux.exceptions.allowed_needed]\n\"mios-portal\" = [\"libc.so.6\"]\n\"libxul.so\" = [\"libfoo.so\"]\n\"plugin-container\" = [\"libfoo.so\"]\n\"libclearkey.so\" = [\"strtab+0\"]\n"
        );
        let tmp_bad = gecko_root(Some(&ex_bad));
        let report_bad = check(&opts(tmp_bad.path()));
        assert_eq!(report_bad.code(), 1, "unapproved DT_NEEDED must fail: {:?}", report_bad.findings);
        assert!(
            report_bad
                .findings
                .iter()
                .any(|f| f.contains("mios-portal") && f.contains("unapproved DT_NEEDED") && f.contains("libfoo.so")),
            "{:?}",
            report_bad.findings
        );
    }
}
