// AI-hint: One native, SSOT-declared generation pipeline for build, install and development.
// AI-related: usr/share/mios/mios.toml, tools/sync-generated.sh
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Plan {
    steps: Vec<Step>,
    unit_projections: Vec<String>,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Step {
    id: String,
    #[serde(default)]
    calls: Vec<Call>,
    #[serde(default)]
    register: bool,
    copy: Option<[String; 2]>,
    snapshot: Option<[String; 2]>,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Call {
    tool: Option<String>,
    script: Option<String>,
    #[serde(default)]
    args: Vec<String>,
}

struct Context {
    root: PathBuf,
    tools: BTreeMap<String, PathBuf>,
    python: Option<PathBuf>,
    bash: Option<PathBuf>,
    git: PathBuf,
}

fn local(root: &Path, relative: &str) -> Result<PathBuf, String> {
    let path = Path::new(relative);
    if path.is_absolute()
        || path
            .components()
            .any(|c| !matches!(c, std::path::Component::Normal(_)))
    {
        return Err(format!(
            "generation path must stay beneath the root: {relative}"
        ));
    }
    let actual_root = root.canonicalize().map_err(|e| e.to_string())?;
    let mut ancestor = root.join(path);
    while !ancestor.exists() {
        if !ancestor.pop() {
            return Err(format!(
                "generation path has no existing ancestor: {relative}"
            ));
        }
    }
    if !ancestor
        .canonicalize()
        .map_err(|e| e.to_string())?
        .starts_with(&actual_root)
    {
        return Err(format!(
            "generation path escapes the root through a link: {relative}"
        ));
    }
    Ok(root.join(path))
}

fn executable(path: &Path) -> bool {
    if !path.is_file() {
        return false;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::metadata(path)
            .map(|m| m.permissions().mode() & 0o111 != 0)
            .unwrap_or(false)
    }
    #[cfg(not(unix))]
    {
        true
    }
}

fn on_path(name: &str) -> Option<PathBuf> {
    let suffix = std::env::consts::EXE_SUFFIX;
    let file = if name.ends_with(suffix) {
        name.to_string()
    } else {
        format!("{name}{suffix}")
    };
    std::env::split_paths(&std::env::var_os("PATH")?)
        .map(|dir| dir.join(&file))
        .find(|path| executable(path))
}

fn native(root: &Path, name: &str) -> Result<PathBuf, String> {
    if name.is_empty() || !name.chars().all(|c| c.is_ascii_alphanumeric() || c == '-') {
        return Err(format!("invalid native generation tool name: {name}"));
    }
    let file = format!("{name}{}", std::env::consts::EXE_SUFFIX);
    if let Some(directory) = std::env::var_os("MIOS_NATIVE_BIN_DIR") {
        let path = PathBuf::from(directory).join(&file);
        return executable(&path).then_some(path).ok_or_else(|| {
            format!("required native tool {file} missing from MIOS_NATIVE_BIN_DIR")
        });
    }
    if name == "mios-gen" {
        return std::env::current_exe().map_err(|e| e.to_string());
    }
    [
        root.join("tools/native/target/release").join(&file),
        PathBuf::from("/usr/bin").join(&file),
        PathBuf::from("/usr/libexec/mios").join(&file),
        PathBuf::from("/opt/mios/bin").join(&file),
    ]
    .into_iter()
    .find(|path| executable(path))
    .or_else(|| on_path(name))
    .ok_or_else(|| format!("required native tool {file} missing; build the SSOT native catalog"))
}

impl Context {
    fn command(&self, program: &Path) -> Command {
        let mut command = Command::new(program);
        command.current_dir(&self.root);
        // Keep each generation hermetic to the selected root, including drop-ins.
        for (name, relative) in [
            ("MIOS_ROOT", ""),
            ("MIOS_TOML_ROOT", ""),
            ("MIOS_TOML", "usr/share/mios/mios.toml"),
            ("MIOS_VENDOR_TOML", "usr/share/mios/mios.toml"),
            ("MIOS_VENDOR_TOML_D", "usr/lib/mios/mios.d"),
            ("MIOS_HOST_TOML", "etc/mios/mios.toml"),
            ("MIOS_HOST_TOML_D", "etc/mios/mios.d"),
            ("MIOS_USER_TOML", ".mios-absent.toml"),
            ("MIOS_USER_TOML_D", ".mios-absent.d"),
            ("MIOS_DRIFT_ROOT", ""),
        ] {
            command.env(name, self.root.join(relative));
        }
        command
    }

    fn run(&self, program: &Path, args: &[String]) -> Result<Output, String> {
        let output = self
            .command(program)
            .args(args)
            .output()
            .map_err(|e| format!("cannot execute {}: {e}", program.display()))?;
        if !output.status.success() {
            // Keep the child's diagnostics and exact exit status in the caller's log.
            use std::io::Write;
            let _ = std::io::stderr().write_all(&output.stderr);
            return Err(format!(
                "{} failed with {}",
                program.display(),
                output.status
            ));
        }
        Ok(output)
    }
}

fn load(root: &Path) -> Result<Plan, String> {
    let data = mios_resolver::resolve_projection(root).map_err(|e| e.to_string())?;
    let value = data
        .get("generation")
        .and_then(|v| v.get("sync"))
        .ok_or("missing [generation.sync] SSOT plan")?;
    let plan: Plan = value
        .clone()
        .try_into()
        .map_err(|e| format!("invalid generation.sync: {e}"))?;
    if plan.steps.is_empty() {
        return Err("generation.sync.steps must not be empty".into());
    }
    let mut ids = std::collections::BTreeSet::new();
    for step in &plan.steps {
        if step.id.is_empty() || !ids.insert(&step.id) {
            return Err(format!("empty or duplicate generation step: {}", step.id));
        }
        let actions = usize::from(step.register)
            + usize::from(step.copy.is_some())
            + usize::from(step.snapshot.is_some())
            + usize::from(!step.calls.is_empty());
        if actions != 1 {
            return Err(format!(
                "generation step {} must declare exactly one action",
                step.id
            ));
        }
        for call in &step.calls {
            if call.tool.is_some() == call.script.is_some() {
                return Err(format!(
                    "generation call in {} must select one tool or script",
                    step.id
                ));
            }
            if let Some(script) = &call.script {
                local(root, script)?;
            }
        }
        for paths in [&step.copy, &step.snapshot].into_iter().flatten() {
            for path in paths {
                local(root, path)?;
            }
        }
    }
    Ok(plan)
}

fn preflight(root: &Path, plan: &Plan) -> Result<Context, String> {
    let git = on_path("git").ok_or("git is required for generation censuses")?;
    let mut tools = BTreeMap::new();
    let mut needs_python = false;
    let mut needs_bash = false;
    for step in &plan.steps {
        for call in &step.calls {
            if let Some(name) = &call.tool {
                tools.insert(name.clone(), native(root, name)?);
            }
            if let Some(script) = &call.script {
                needs_python = true;
                if !local(root, script)?.is_file() {
                    return Err(format!("required generation script missing: {script}"));
                }
            }
        }
        for (paths, snapshot) in [(&step.copy, false), (&step.snapshot, true)] {
            if let Some(paths) = paths {
                if !local(root, &paths[0])?.is_file() {
                    return Err(format!("required generation source missing: {}", paths[0]));
                }
                needs_bash |= snapshot;
            }
        }
    }
    let python = if needs_python {
        let candidates = std::env::var_os("PYTHON")
            .map(PathBuf::from)
            .into_iter()
            .chain(["python3", "python", "py"].into_iter().filter_map(on_path));
        Some(
            candidates
                .into_iter()
                .find(|path| {
                    Command::new(path)
                        .args(["-c", "import sys; sys.exit(0)"])
                        .output()
                        .is_ok_and(|o| o.status.success())
                })
                .ok_or(
                    "a working Python is required by the remaining declared projection adapters",
                )?,
        )
    } else {
        None
    };
    let bash = if needs_bash {
        Some(
            on_path("bash")
                .ok_or("bash is required by the declared environment snapshot adapter")?,
        )
    } else {
        None
    };
    let context = Context {
        root: root.to_path_buf(),
        tools,
        python,
        bash,
        git,
    };
    // A stale unit generator must fail before ports, globals or any other writes.
    if !plan.unit_projections.is_empty() {
        let tool = context
            .tools
            .get("mios-unit-gen")
            .ok_or("unit projections require mios-unit-gen in the plan")?;
        let output = context.run(tool, &["--list-projections".into()])?;
        let advertised = String::from_utf8(output.stdout).map_err(|e| e.to_string())?;
        for projection in &plan.unit_projections {
            if !advertised.lines().any(|line| line.trim() == projection) {
                return Err(format!(
                    "mios-unit-gen does not advertise {projection}; rebuild from this checkout"
                ));
            }
        }
    }
    context.run(&context.git, &["rev-parse".into(), "--git-dir".into()])?;
    // This validates index readability without registering files or refreshing it.
    context.run(&context.git, &["ls-files".into(), "-z".into()])?;
    Ok(context)
}

fn replace(path: &Path, bytes: &[u8]) -> Result<(), String> {
    let previous = match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.is_file() => Some(metadata.permissions()),
        Ok(_) => {
            return Err(format!(
                "generation output must be a regular file: {}",
                path.display()
            ))
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => None,
        Err(error) => return Err(error.to_string()),
    };
    if fs::read(path).is_ok_and(|old| old == bytes) {
        return Ok(());
    }
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let mut temporary =
        tempfile::NamedTempFile::new_in(path.parent().ok_or("output has no parent")?)
            .map_err(|e| e.to_string())?;
    use std::io::Write;
    temporary.write_all(bytes).map_err(|e| e.to_string())?;
    if let Some(permissions) = previous {
        temporary
            .as_file()
            .set_permissions(permissions)
            .map_err(|e| e.to_string())?;
    }
    #[cfg(unix)]
    if !path.exists() {
        use std::os::unix::fs::PermissionsExt;
        temporary
            .as_file()
            .set_permissions(fs::Permissions::from_mode(0o644))
            .map_err(|e| e.to_string())?;
    }
    temporary
        .persist(path)
        .map_err(|e| format!("cannot replace {}: {}", path.display(), e.error))?;
    Ok(())
}

#[cfg(windows)]
fn strip_verbatim(path: &Path) -> PathBuf {
    let s = path.to_string_lossy();
    if let Some(stripped) = s.strip_prefix(r"\\?\") {
        PathBuf::from(stripped)
    } else {
        path.to_path_buf()
    }
}

#[cfg(not(windows))]
fn strip_verbatim(path: &Path) -> PathBuf {
    path.to_path_buf()
}

pub fn run(root: &Path, plan_only: bool) -> Result<(), String> {
    let root = strip_verbatim(&root.canonicalize().map_err(|e| e.to_string())?);
    let plan = load(&root)?;
    let context = preflight(&root, &plan)?;
    if plan_only {
        println!(
            "{}",
            serde_json::to_string_pretty(&plan).map_err(|e| e.to_string())?
        );
        return Ok(());
    }
    for (index, step) in plan.steps.iter().enumerate() {
        println!(
            "[mios-gen sync] {}/{} [{}]",
            index + 1,
            plan.steps.len(),
            step.id
        );
        if step.register {
            let output = context.run(
                &context.git,
                &[
                    "ls-files".into(),
                    "--others".into(),
                    "--exclude-standard".into(),
                    "-z".into(),
                ],
            )?;
            let paths = String::from_utf8(output.stdout).map_err(|e| e.to_string())?;
            for path in paths.split('\0').filter(|path| !path.is_empty()) {
                context.run(
                    &context.git,
                    &["add".into(), "-N".into(), "--".into(), path.into()],
                )?;
                println!("[mios-gen sync] registered intent-to-add: {path}");
            }
        } else if let Some([source, target]) = &step.copy {
            replace(
                &local(&root, target)?,
                &fs::read(local(&root, source)?).map_err(|e| e.to_string())?,
            )?;
        } else if let Some([source, target]) = &step.snapshot {
            let output = context
                .command(context.bash.as_ref().ok_or("missing snapshot adapter")?)
                .env_clear()
                .env("PATH", std::env::var_os("PATH").unwrap_or_default())
                .env("HOME", "/nonexistent")
                .env("MIOS_VENDOR_TOML", root.join("usr/share/mios/mios.toml"))
                .env("MIOS_TOML_ROOT", &root)
                .arg(local(&root, source)?)
                .output()
                .map_err(|e| e.to_string())?;
            if !output.status.success() {
                use std::io::Write;
                let _ = std::io::stderr().write_all(&output.stderr);
                return Err(format!(
                    "environment snapshot failed with {}",
                    output.status
                ));
            }
            if output.stdout.is_empty() {
                return Err("environment snapshot produced no values".into());
            }
            replace(&local(&root, target)?, &output.stdout)?;
        } else {
            for call in &step.calls {
                let mut args = Vec::new();
                let program = if let Some(script) = &call.script {
                    args.push(local(&root, script)?.to_string_lossy().into_owned());
                    context.python.as_ref().ok_or("missing Python adapter")?
                } else {
                    context
                        .tools
                        .get(call.tool.as_ref().ok_or("missing native tool")?)
                        .ok_or("native tool not preflighted")?
                };
                let root_str = strip_verbatim(&root).to_string_lossy().into_owned();
                args.extend(
                    call.args
                        .iter()
                        .map(|arg| {
                            let replaced = arg.replace("{root}", &root_str);
                            #[cfg(windows)]
                            {
                                replaced.replace('/', "\\")
                            }
                            #[cfg(not(windows))]
                            {
                                replaced
                            }
                        }),
                );
                context
                    .run(program, &args)
                    .map_err(|e| format!("{}: {e}", step.id))?;
            }
        }
    }
    println!(
        "[mios-gen sync] completed {} declared stages",
        plan.steps.len()
    );
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn paths_cannot_escape_selected_root() {
        let root = tempfile::tempdir().unwrap();
        for path in ["../outside", "/etc/passwd", "safe/../../outside"] {
            assert!(local(root.path(), path).is_err());
        }
        assert!(local(root.path(), "usr/share/mios/mios.toml").is_ok());
    }
    #[test]
    fn empty_and_ambiguous_plans_fail_before_mutation() {
        let root = tempfile::tempdir().unwrap();
        fs::create_dir_all(root.path().join("usr/share/mios")).unwrap();
        let path = root.path().join("usr/share/mios/mios.toml");
        fs::write(&path, "[generation.sync]\nunit_projections=[]\nsteps=[]\n").unwrap();
        assert!(load(root.path()).unwrap_err().contains("must not be empty"));
        fs::write(&path, "[generation.sync]\nunit_projections=[]\n[[generation.sync.steps]]\nid='bad'\nregister=true\ncopy=['one','two']\n").unwrap();
        assert!(load(root.path())
            .unwrap_err()
            .contains("exactly one action"));
    }
    #[test]
    fn replacement_preserves_bytes_on_output_failure() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("output");
        fs::write(&path, b"previous").unwrap();
        replace(&path, b"next").unwrap();
        assert_eq!(fs::read(&path).unwrap(), b"next");
        assert!(replace(&path.join("impossible"), b"bad").is_err());
        assert_eq!(fs::read(&path).unwrap(), b"next");
    }
}
