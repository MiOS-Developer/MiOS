// AI-hint: `mios-gen build-hyprland` -- the compositor's own MiOS patch series ([desktop.hyprland.compositor].patches).
// The plugins have carried reviewed MiOS patches for a long time (mios-gen build-hyprland-plugin); a bug inside the
// compositor needs the same thing for Hyprland itself. This rebuilds the INSTALLED release's source RPM (the SRPM of the
// ABI-held [desktop.hyprland.hyprglass].hyprland_version; --srpm or `dnf download --source`) with the SSOT patches
// applied in order after the spec's own %prep patches: each patch is checked against the pristine source first and a
// patch that no longer applies fails the build naming it (never silently skipped); the Release gets the SSOT
// release_tag (2.fc44 -> 2.mios2.fc44), so the result upgrades the stock package and `rpm -q hyprland` shows it is
// patched. Build parallelism is the plugin builds' [desktop.hyprland.hyprglass].build_jobs. Runs only inside
// podman-MiOS-DEV; the RPMs land in --out (the bake installs them; a live host installs them with rpm-ostree/dnf and the
// patch takes effect at the next compositor start -- a running Hyprland is never touched).
// First patch: hyprland-screenshare-no-monitor.patch (Hyprland [packages.hyprland_patch].version SEGV in Screenshare::CScreenshareSession::init
// on a toplevel capture of a window without a live monitor).
// AI-related: usr/share/mios/mios.toml [desktop.hyprland.compositor], usr/share/mios/hyprland/patches/hyprland-screenshare-no-monitor.patch,
// tools/native/mios-gen/src/render.rs (hyprglass_build: the plugin patch series), automation/65-bake-hyprland.sh,
// tests/test-hyprland-capture-patch-live.py
use std::fs;
use std::path::Path;
use std::process::{Command, Stdio};
use toml::Value;

fn command(program: &str, args: &[String], cwd: &Path) -> Result<(), String> {
    let status = Command::new(program)
        .args(args)
        .current_dir(cwd)
        .status()
        .map_err(|e| format!("{program}: {e}"))?;
    if status.success() {
        Ok(())
    } else {
        Err(format!("{program} {} failed: {status}", args.join(" ")))
    }
}
fn output(program: &str, args: &[&str], cwd: &Path) -> Result<String, String> {
    let out = Command::new(program)
        .args(args)
        .current_dir(cwd)
        .stderr(Stdio::inherit())
        .output()
        .map_err(|e| format!("{program}: {e}"))?;
    if !out.status.success() {
        return Err(format!("{program} {} failed", args.join(" ")));
    }
    Ok(String::from_utf8_lossy(&out.stdout).trim().to_owned())
}

/// The compositor patch series and release tag from SSOT.
pub struct Series {
    pub patches: Vec<String>,
    pub release_tag: String,
}

pub fn series(data: &Value) -> Result<Series, String> {
    let c = data
        .get("desktop")
        .and_then(|d| d.get("hyprland"))
        .and_then(|h| h.get("compositor"))
        .ok_or("No [desktop.hyprland.compositor] in SSOT")?;
    let patches = c
        .get("patches")
        .and_then(Value::as_array)
        .ok_or("desktop.hyprland.compositor.patches must be a list")?
        .iter()
        .map(|p| {
            p.as_str()
                .map(str::to_owned)
                .ok_or("desktop.hyprland.compositor.patches entries must be strings")
        })
        .collect::<Result<Vec<_>, _>>()?;
    for p in &patches {
        if Path::new(p).is_absolute()
            || Path::new(p)
                .components()
                .any(|c| !matches!(c, std::path::Component::Normal(_)))
            || !p.ends_with(".patch")
        {
            return Err(format!("desktop.hyprland.compositor.patches: {p} must be a repository-relative .patch path"));
        }
    }
    let release_tag = c
        .get("release_tag")
        .and_then(Value::as_str)
        .ok_or("desktop.hyprland.compositor.release_tag missing")?
        .to_owned();
    if release_tag.is_empty() || !release_tag.bytes().all(|b| b.is_ascii_alphanumeric()) {
        return Err("desktop.hyprland.compositor.release_tag must be [A-Za-z0-9]+".into());
    }
    Ok(Series {
        patches,
        release_tag,
    })
}

/// Select the compositor package from the same stable ABI hold used by every plugin build.
pub fn held_version(data: &Value) -> Result<&str, String> {
    let version = data
        .get("desktop")
        .and_then(|d| d.get("hyprland"))
        .and_then(|h| h.get("hyprglass"))
        .and_then(|h| h.get("hyprland_version"))
        .and_then(Value::as_str)
        .ok_or("hyprglass.hyprland_version missing")?;
    let parts: Vec<_> = version.split('.').collect();
    if parts.len() != 3
        || parts
            .iter()
            .any(|part| part.is_empty() || !part.bytes().all(|b| b.is_ascii_digit()))
    {
        return Err(
            "hyprglass.hyprland_version must be a stable numeric major.minor.patch release".into(),
        );
    }
    Ok(version)
}

pub fn package_spec(data: &Value) -> Result<String, String> {
    Ok(format!("hyprland-{}", held_version(data)?))
}

pub const FIRST_PATCH: usize = 1000;

/// The compositor's plugin SDK, as the main package's %files lists it.
const SDK_FILES: [&str; 2] = ["%{_includedir}/hyprland", "%{_datadir}/pkgconfig/hyprland.pc"];

/// An `rm` line that deletes SDK paths from the buildroot: `Some(None)` drops it (it removed only the
/// SDK), `Some(Some(line))` keeps it without them, `None` leaves every other line alone.
fn keep_sdk(line: &str) -> Option<Option<String>> {
    let words: Vec<&str> = line.split_whitespace().collect();
    if words.first() != Some(&"rm") {
        return None;
    }
    let is_sdk = |w: &str| {
        let w = w.trim_end_matches('/');
        SDK_FILES
            .iter()
            .any(|f| w == format!("%{{buildroot}}{f}") || w == format!("$RPM_BUILD_ROOT{f}"))
    };
    if !words.iter().any(|w| is_sdk(w)) {
        return None;
    }
    let rest: Vec<&str> = words.iter().copied().filter(|w| !is_sdk(w)).collect();
    if rest.iter().skip(1).all(|w| w.starts_with('-')) {
        return Some(None);
    }
    let indent = &line[..line.len() - line.trim_start().len()];
    Some(Some(format!("{indent}{}", rest.join(" "))))
}

/// Adds the series to the SRPM spec: PatchN tags after the spec's last Patch tag (a spec with none: after the main
/// package's last Source tag), `%patch -P N -p1` after the spec's own conditional patching in %prep (right after the
/// %autosetup block), and the release tag on the main Release. An %autosetup without -N applies every declared patch
/// itself, at its own strip level; with no patches of its own the spec gets -N, so the series is applied exactly once,
/// explicitly. A spec that lets %autosetup apply its own patches is refused: the series could not be ordered after them.
/// The plugin SDK (headers, hyprland.pc) stays in the rebuilt main package even where the spec strips it.
pub fn rewrite_spec(spec: &str, names: &[String], tag: &str) -> Result<String, String> {
    let lines: Vec<&str> = spec.lines().collect();
    let release = lines
        .iter()
        .position(|l| l.starts_with("Release:"))
        .ok_or("spec has no Release")?;
    if !lines[release].contains("%{?dist}") {
        return Err("spec Release has no %{?dist} to tag".into());
    }
    let setup = lines
        .iter()
        .position(|l| l.trim_start().starts_with("%autosetup"))
        .ok_or("spec has no %autosetup in %prep")?;
    let autosetup_applies = !lines[setup].split_whitespace().any(|w| w == "-N");
    let own_patch = lines.iter().rposition(|l| l.starts_with("Patch"));
    if autosetup_applies && own_patch.is_some() {
        return Err(
            "spec's %autosetup applies its own patches (no -N): the series cannot follow them"
                .into(),
        );
    }
    // The main package's preamble ends at its first %package or %description.
    let preamble_end = lines
        .iter()
        .position(|l| l.starts_with("%package") || l.starts_with("%description"))
        .unwrap_or(lines.len());
    let last_patch = match own_patch {
        Some(i) => i,
        None => lines[..preamble_end]
            .iter()
            .rposition(|l| l.starts_with("Source"))
            .ok_or("spec has no Patch or Source tag to follow")?,
    };
    // the spec's own patch block right after %autosetup: an %if ... %endif (or nothing)
    let mut apply_at = setup + 1;
    if lines
        .get(apply_at)
        .is_some_and(|l| l.trim_start().starts_with("%if"))
    {
        apply_at = lines[apply_at..]
            .iter()
            .position(|l| l.trim() == "%endif")
            .map(|i| apply_at + i + 1)
            .ok_or("unterminated %if after %autosetup")?;
    }
    // The plugins build against the installed compositor's SDK; a spec that strips it in %install
    // (0.57: "Plugin development files, unusable without the vendored headers") keeps it here, and
    // the main %files owns it. The vendored libraries' headers come from the SRPM at plugin build time.
    let main_files = lines.iter().position(|l| l.trim_end() == "%files");
    let owns_sdk = main_files.is_some_and(|f| {
        lines[f + 1..]
            .iter()
            .take_while(|l| !l.starts_with("%files") && !l.starts_with("%changelog"))
            .any(|l| l.trim_end().trim_end_matches('/') == SDK_FILES[0])
    });
    let mut out = Vec::new();
    for (i, l) in lines.iter().enumerate() {
        if let Some(kept) = keep_sdk(l) {
            if let Some(kept) = kept {
                out.push(kept);
            }
            continue;
        }
        if i == release {
            out.push(l.replacen("%{?dist}", &format!(".{tag}%{{?dist}}"), 1));
        } else if i == setup && autosetup_applies {
            out.push(l.replacen("%autosetup", "%autosetup -N", 1));
        } else {
            out.push((*l).to_owned());
        }
        if i == last_patch {
            for (k, n) in names.iter().enumerate() {
                out.push(format!("Patch{}:      {n}", FIRST_PATCH + k));
            }
        }
        if Some(i) == main_files && !owns_sdk {
            out.push("# MiOS: the plugin SDK the hyprglass/hypr3d builds compile against".into());
            out.extend(SDK_FILES.iter().map(|f| (*f).to_owned()));
        }
        if i + 1 == apply_at {
            out.push(
                "# MiOS compositor patch series ([desktop.hyprland.compositor].patches)".into(),
            );
            for k in 0..names.len() {
                out.push(format!("%patch -P {} -p1", FIRST_PATCH + k));
            }
        }
    }
    Ok(out.join("\n") + "\n")
}

pub fn run(
    root: &Path,
    srpm: Option<&Path>,
    out_dir: &Path,
    check_only: bool,
) -> Result<String, String> {
    if !Path::new("/run/.containerenv").exists() {
        return Err("Run this build inside podman-MiOS-DEV".into());
    }
    let data = mios_resolver::resolve_merged(Some(root), false).map_err(|e| e.to_string())?;
    let s = series(&data)?;
    let h = data
        .get("desktop")
        .and_then(|d| d.get("hyprland"))
        .and_then(|h| h.get("hyprglass"))
        .ok_or("No [desktop.hyprland.hyprglass]")?;
    let held = held_version(&data)?;
    let jobs = h
        .get("build_jobs")
        .and_then(Value::as_integer)
        .filter(|n| (1..=64).contains(n))
        .ok_or("Invalid hyprglass.build_jobs")?;
    let work = tempfile::Builder::new()
        .prefix("mios-hyprland-build-")
        .tempdir()
        .map_err(|e| e.to_string())?;
    let top = work.path();
    for d in ["SOURCES", "SPECS", "BUILD", "RPMS", "SRPMS", "src"] {
        fs::create_dir_all(top.join(d)).map_err(|e| e.to_string())?;
    }
    let source = match srpm {
        Some(p) => p.canonicalize().map_err(|e| e.to_string())?,
        None => {
            let release = output("rpm", &["-E", "%fedora"], top)?;
            let package = output("rpm", &["-q", "hyprland"], top)?;
            command(
                "dnf",
                &[
                    format!("--releasever={release}"),
                    "download".into(),
                    "--source".into(),
                    "--destdir".into(),
                    top.display().to_string(),
                    package,
                ],
                top,
            )?;
            fs::read_dir(top)
                .map_err(|e| e.to_string())?
                .flatten()
                .map(|e| e.path())
                .find(|p| p.to_string_lossy().ends_with(".src.rpm"))
                .ok_or("no SRPM downloaded")?
        }
    };
    let version = output(
        "rpm",
        &[
            "-qp",
            "--qf",
            "%{VERSION}",
            source.to_str().ok_or("SRPM path")?,
        ],
        top,
    )?;
    if version != held {
        return Err(format!(
            "Compositor ABI hold: SRPM is Hyprland {version}, SSOT holds {held}"
        ));
    }
    let mut rpm2 = Command::new("rpm2cpio")
        .arg(&source)
        .stdout(Stdio::piped())
        .spawn()
        .map_err(|e| e.to_string())?;
    let st = Command::new("cpio")
        .args(["-id", "--no-absolute-filenames", "--quiet"])
        .current_dir(top.join("SOURCES"))
        .stdin(rpm2.stdout.take().ok_or("rpm2cpio")?)
        .status()
        .map_err(|e| e.to_string())?;
    if !st.success() || !rpm2.wait().map_err(|e| e.to_string())?.success() {
        return Err("SRPM extraction failed".into());
    }
    let spec_src = fs::read_dir(top.join("SOURCES"))
        .map_err(|e| e.to_string())?
        .flatten()
        .map(|e| e.path())
        .find(|p| p.extension().is_some_and(|x| x == "spec"))
        .ok_or("SRPM has no spec")?;
    // Every patch must apply to the pristine release, in order, before anything is built.
    let tarball = top
        .join("SOURCES")
        .join(format!("hyprland-{version}.tar.gz"));
    command(
        "tar",
        &[
            "-xzf".into(),
            tarball.display().to_string(),
            "-C".into(),
            top.join("src").display().to_string(),
        ],
        top,
    )?;
    let tree = top.join("src").join(format!("Hyprland-{version}"));
    let mut names = Vec::new();
    for p in &s.patches {
        let path = root.join(p);
        if !path.is_file() {
            return Err(format!("MiOS compositor patch {p} not found"));
        }
        let ok = Command::new("patch")
            .args(["-p1", "--forward", "--batch", "-s", "-i"])
            .arg(&path)
            .current_dir(&tree)
            .status()
            .map_err(|e| e.to_string())?;
        if !ok.success() {
            return Err(format!(
                "MiOS compositor patch {p} no longer applies to Hyprland {version}"
            ));
        }
        let name = path.file_name().unwrap().to_string_lossy().into_owned();
        fs::copy(&path, top.join("SOURCES").join(&name)).map_err(|e| e.to_string())?;
        names.push(name);
    }
    let spec = rewrite_spec(
        &fs::read_to_string(&spec_src).map_err(|e| e.to_string())?,
        &names,
        &s.release_tag,
    )?;
    if check_only {
        return Ok(format!(
            "{} MiOS compositor patch(es) and the RPM spec apply to Hyprland {version}\n",
            names.len()
        ));
    }
    let spec_path = top.join("SPECS").join("hyprland-mios.spec");
    fs::write(&spec_path, spec).map_err(|e| e.to_string())?;
    let release = output("rpm", &["-E", "%fedora"], top)?;
    command(
        "dnf",
        &[
            format!("--releasever={release}"),
            "-y".into(),
            "install".into(),
            "rpm-build".into(),
        ],
        top,
    )?;
    command(
        "dnf",
        &[
            format!("--releasever={release}"),
            "-y".into(),
            "builddep".into(),
            spec_path.display().to_string(),
        ],
        top,
    )?;
    command(
        "rpmbuild",
        &[
            "-bb".into(),
            "--define".into(),
            format!("_topdir {}", top.display()),
            "--define".into(),
            format!("_smp_build_ncpus {jobs}"),
            "--define".into(),
            format!("_smp_mflags -j{jobs}"),
            spec_path.display().to_string(),
        ],
        top,
    )?;
    fs::create_dir_all(out_dir).map_err(|e| e.to_string())?;
    // Plugin SDKs must reuse the original provider's source bundle. The patched
    // installed NEVRA has a local release tag and cannot be downloaded upstream.
    let source_dir = out_dir.join("source");
    fs::create_dir_all(&source_dir).map_err(|e| e.to_string())?;
    fs::copy(
        &source,
        source_dir.join(source.file_name().ok_or("SRPM filename")?),
    )
    .map_err(|e| e.to_string())?;
    let mut built = Vec::new();
    for e in walkdir::WalkDir::new(top.join("RPMS"))
        .into_iter()
        .flatten()
    {
        if e.path().extension().is_some_and(|x| x == "rpm") {
            let dest = out_dir.join(e.file_name());
            fs::copy(e.path(), &dest).map_err(|e| e.to_string())?;
            built.push(dest.display().to_string());
        }
    }
    if !built
        .iter()
        .any(|b| b.contains(&format!("hyprland-{version}-")) && b.contains(&s.release_tag))
    {
        return Err("rpmbuild produced no tagged hyprland RPM".into());
    }
    Ok(format!(
        "Hyprland {version} with {} MiOS patch(es) [{}]:\n{}\n",
        names.len(),
        names.join(", "),
        built.join("\n")
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn package_selection_uses_the_plugin_abi_hold() {
        let data: Value = "[desktop.hyprland.hyprglass]\nhyprland_version='0.56.2'\n"
            .parse()
            .unwrap();
        assert_eq!(package_spec(&data).unwrap(), "hyprland-0.56.2");
        let changed: Value = "[desktop.hyprland.hyprglass]\nhyprland_version='0.57.0'\n"
            .parse()
            .unwrap();
        assert_eq!(package_spec(&changed).unwrap(), "hyprland-0.57.0");
    }

    #[test]
    fn package_selection_rejects_missing_or_unsafe_holds() {
        for version in [
            "",
            "0.56",
            "v0.56.2",
            "0.56.*",
            "0.56.2 --nogpgcheck",
            "0.56.2\nother",
            "0.56.2-rc1",
        ] {
            let mut data: Value = "[desktop.hyprland.hyprglass]\nhyprland_version='0.56.2'\n"
                .parse()
                .unwrap();
            data["desktop"]["hyprland"]["hyprglass"]["hyprland_version"] =
                Value::String(version.into());
            assert!(package_spec(&data).is_err(), "{version:?}");
        }
        assert!(package_spec(&Value::Table(Default::default())).is_err());
    }

    const SPEC: &str = "Name: hyprland\nRelease:        2%{?dist}\nPatch0:         https://x/fix.patch\n\n%package -n hyprlock\nRelease:        1%{?dist}\n%prep\n%autosetup -N -n Hyprland-0.56.2\n%if 0%{?fedora} <= 43\n%patch -P 0 -p1\n%endif\n\n# Unpack\ntar -xzf x\n";

    #[test]
    fn spec_gets_the_series_after_its_own_patches() {
        let out = rewrite_spec(SPEC, &["a.patch".into(), "b.patch".into()], "mios1").unwrap();
        let l: Vec<&str> = out.lines().collect();
        assert_eq!(l[1], "Release:        2.mios1%{?dist}");
        assert!(
            out.contains("Release:        1%{?dist}"),
            "subpackage releases untouched"
        );
        let p0 = l.iter().position(|x| x.starts_with("Patch0")).unwrap();
        assert_eq!(l[p0 + 1], "Patch1000:      a.patch");
        assert_eq!(l[p0 + 2], "Patch1001:      b.patch");
        let endif = l.iter().position(|x| *x == "%endif").unwrap();
        assert!(l[endif + 1].starts_with("# MiOS"));
        assert_eq!(l[endif + 2], "%patch -P 1000 -p1");
        assert_eq!(l[endif + 3], "%patch -P 1001 -p1");
    }

    #[test]
    fn spec_without_conditional_block_applies_right_after_setup() {
        let spec = "Release: 1%{?dist}\nPatch0: p\n%prep\n%autosetup -N -n X\nmake\n";
        let out = rewrite_spec(spec, &["a.patch".into()], "mios1").unwrap();
        let l: Vec<&str> = out.lines().collect();
        let setup = l.iter().position(|x| x.starts_with("%autosetup")).unwrap();
        assert_eq!(l[setup + 2], "%patch -P 1000 -p1");
    }

    /// The shape of the Fedora 45 (0.57.2) SRPM: no Patch tags and an %autosetup that would apply patches itself.
    const SPEC_057: &str = "Name: hyprland\nRelease:        %{hyprland_release}%{?dist}\nSource0: h.tar.gz\n# Subpackage sources\nSource41: i.tar.gz\n\nBuildRequires:  cmake\n%description\nx\n%package -n hyprlock\nRelease:        1.%{?dist}\nSource99: not-the-main-preamble\n%prep\n%autosetup -n Hyprland-%{hyprland_version}\n\ntar -xzf x\n";

    #[test]
    fn spec_without_patches_gets_the_series_after_its_sources_once() {
        let out = rewrite_spec(SPEC_057, &["a.patch".into(), "b.patch".into()], "mios3").unwrap();
        let l: Vec<&str> = out.lines().collect();
        assert_eq!(l[1], "Release:        %{hyprland_release}.mios3%{?dist}");
        let src = l.iter().position(|x| x.starts_with("Source41")).unwrap();
        assert_eq!(l[src + 1], "Patch1000:      a.patch");
        assert_eq!(l[src + 2], "Patch1001:      b.patch");
        assert!(
            !out.contains("Source99: not-the-main-preamble\nPatch"),
            "{out}"
        );
        let setup = l.iter().position(|x| x.starts_with("%autosetup")).unwrap();
        // -N: %autosetup must not apply the series a second time at its own strip level
        assert_eq!(l[setup], "%autosetup -N -n Hyprland-%{hyprland_version}");
        assert!(l[setup + 1].starts_with("# MiOS"));
        assert_eq!(l[setup + 2], "%patch -P 1000 -p1");
        assert_eq!(l[setup + 3], "%patch -P 1001 -p1");
        assert_eq!(out.matches("%patch -P 1000").count(), 1);
    }

    // The 0.57 spec's %install strips the plugin SDK; the 0.56 spec shipped it in the main %files.
    const SPEC_SDK: &str = "Release: 1%{?dist}\nSource0: s\n%prep\n%autosetup -n X\n%install\n%cmake_install\n# Plugin development files\nrm -rf %{buildroot}%{_includedir}/hyprland %{buildroot}%{_datadir}/pkgconfig/hyprland.pc\nrm -f %{buildroot}%{_datadir}/x.txt %{buildroot}%{_datadir}/pkgconfig/hyprland.pc\n%files\n%{_bindir}/Hyprland\n%files -n hyprlock\n%{_bindir}/hyprlock\n";

    #[test]
    fn stripped_plugin_sdk_is_kept_in_the_main_package() {
        let out = rewrite_spec(SPEC_SDK, &[], "m").unwrap();
        assert!(!out.contains("%{buildroot}%{_includedir}/hyprland"), "{out}");
        assert!(!out.contains("%{buildroot}%{_datadir}/pkgconfig/hyprland.pc"), "{out}");
        // other paths on an rm line still go
        assert!(out.contains("\nrm -f %{buildroot}%{_datadir}/x.txt\n"), "{out}");
        let l: Vec<&str> = out.lines().collect();
        let files = l.iter().position(|x| *x == "%files").unwrap();
        let sub = l.iter().position(|x| *x == "%files -n hyprlock").unwrap();
        let main = &l[files..sub];
        assert!(main.contains(&"%{_includedir}/hyprland"), "{out}");
        assert!(main.contains(&"%{_datadir}/pkgconfig/hyprland.pc"), "{out}");
        assert_eq!(out.matches("%{_includedir}/hyprland").count(), 1, "{out}");
    }

    #[test]
    fn spec_that_ships_the_sdk_is_left_alone() {
        let spec = "Release: 1%{?dist}\nSource0: s\n%prep\n%autosetup -n X\n%install\nrm -rf subprojects/udis86\n%files\n%{_bindir}/Hyprland\n%{_includedir}/hyprland/\n%{_datadir}/pkgconfig/hyprland.pc\n";
        let out = rewrite_spec(spec, &[], "m").unwrap();
        assert!(out.contains("\nrm -rf subprojects/udis86\n"), "{out}");
        assert_eq!(out.matches("%{_includedir}/hyprland").count(), 1, "{out}");
        assert!(!out.contains("# MiOS: the plugin SDK"), "{out}");
    }

    #[test]
    fn autosetup_applying_its_own_patches_is_refused() {
        let spec = "Release: 1%{?dist}\nSource0: s\nPatch0: p\n%prep\n%autosetup -n X\n";
        assert!(rewrite_spec(spec, &["a.patch".into()], "m")
            .unwrap_err()
            .contains("-N"));
    }

    #[test]
    fn broken_specs_and_series_fail_loudly() {
        assert!(
            rewrite_spec("Release: 1\nPatch0: p\n%autosetup\n", &[], "m")
                .unwrap_err()
                .contains("dist")
        );
        assert!(rewrite_spec("Release: 1%{?dist}\n%autosetup\n", &[], "m")
            .unwrap_err()
            .contains("Patch"));
        let bad: Value = toml::from_str(
            "[desktop.hyprland.compositor]\npatches = [\"../x.patch\"]\nrelease_tag = \"mios1\"\n",
        )
        .unwrap();
        assert!(series(&bad).is_err());
        let tag: Value = toml::from_str(
            "[desktop.hyprland.compositor]\npatches = []\nrelease_tag = \"mios-1\"\n",
        )
        .unwrap();
        assert!(series(&tag).is_err());
    }

    #[test]
    fn the_shipped_series_is_valid_and_present() {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../..");
        let text = fs::read_to_string(root.join("usr/share/mios/mios.toml")).unwrap();
        let data: Value = toml::from_str(&text).unwrap();
        let s = series(&data).unwrap();
        assert!(s
            .patches
            .iter()
            .any(|p| p.ends_with("hyprland-screenshare-no-monitor.patch")));
        for p in &s.patches {
            assert!(root.join(p).is_file(), "{p}");
        }
    }
}
