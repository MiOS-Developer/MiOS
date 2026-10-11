// AI-hint: Shared fail-closed filesystem, TOML and native-tool primitives for drift audits.
// AI-related: src/mios-rs/miosd/src/drift/mod.rs, tools/native/mios-resolver
use super::{DriftCtx, Verdict};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

pub type Audit = Result<String, String>;

pub fn verdict(result: Audit) -> Verdict {
    match result {
        Ok(message) => Verdict::Pass(message),
        Err(message) => Verdict::Fail(message),
    }
}

pub fn read(root: &Path, name: &str) -> Result<String, String> {
    fs::read_to_string(root.join(name)).map_err(|e| format!("{name}: {e}"))
}

pub fn ssot(ctx: &DriftCtx) -> Result<toml::Value, String> {
    let text = read(&ctx.root, "usr/share/mios/mios.toml")?;
    text.parse().map_err(|e| format!("mios.toml: {e}"))
}

pub fn at<'a>(value: &'a toml::Value, key: &str) -> Result<&'a toml::Value, String> {
    key.split('.')
        .try_fold(value, |v, k| v.get(k))
        .ok_or_else(|| format!("SSOT {key} is missing"))
}

pub fn strings(value: &toml::Value, key: &str) -> Result<Vec<String>, String> {
    at(value, key)?
        .as_array()
        .ok_or_else(|| format!("SSOT {key} must be an array"))?
        .iter()
        .map(|v| {
            v.as_str()
                .map(str::to_owned)
                .ok_or_else(|| format!("SSOT {key} contains a non-string"))
        })
        .collect()
}

pub fn files(root: &Path, under: &str) -> Result<Vec<String>, String> {
    fn visit(root: &Path, path: &Path, out: &mut Vec<String>) -> Result<(), String> {
        for entry in fs::read_dir(path).map_err(|e| format!("{}: {e}", path.display()))? {
            let entry = entry.map_err(|e| e.to_string())?;
            let kind = entry.file_type().map_err(|e| e.to_string())?;
            let name = entry.file_name();
            let name = name.to_string_lossy();
            if kind.is_dir() {
                if !matches!(
                    name.as_ref(),
                    ".git"
                        | ".devloop"
                        | ".worktrees"
                        | "target"
                        | "node_modules"
                        | "__pycache__"
                        | ".venv"
                        | "venv"
                ) {
                    visit(root, &entry.path(), out)?;
                }
            } else if kind.is_file() {
                out.push(
                    entry
                        .path()
                        .strip_prefix(root)
                        .map_err(|e| e.to_string())?
                        .to_string_lossy()
                        .replace('\\', "/"),
                );
            }
        }
        Ok(())
    }
    let path = root.join(under);
    if !path.is_dir() {
        return Err(format!("required source directory {under} is missing"));
    }
    let mut out = Vec::new();
    visit(root, &path, &mut out)?;
    out.sort();
    Ok(out)
}

pub fn tracked(ctx: &DriftCtx) -> Result<Vec<String>, String> {
    let output = Command::new("git")
        .args(["-C"])
        .arg(&ctx.root)
        .args(["ls-files", "-z"])
        .output()
        .map_err(|e| format!("git ls-files: {e}"))?;
    if !output.status.success() {
        return Err(format!(
            "git ls-files {}: {}",
            output.status,
            String::from_utf8_lossy(&output.stderr)
        ));
    }
    let text = String::from_utf8(output.stdout).map_err(|e| format!("git filenames: {e}"))?;
    let paths: Vec<_> = text
        .split('\0')
        .filter(|s| !s.is_empty())
        .map(str::to_owned)
        .collect();
    if paths.is_empty() {
        return Err("git ls-files returned no subjects".into());
    }
    Ok(paths)
}

pub fn finish(subjects: usize, errors: Vec<String>, label: &str) -> Audit {
    if subjects == 0 {
        return Err(format!("{label}: no subjects examined"));
    }
    if errors.is_empty() {
        Ok(format!("{label}: {subjects} subject(s) examined"))
    } else {
        Err(errors.join("\n"))
    }
}

pub fn native(ctx: &DriftCtx, name: &str, args: &[&str]) -> Verdict {
    verdict((|| {
        let filename = format!("{name}{}", std::env::consts::EXE_SUFFIX);
        let override_name = format!("{}_BIN", name.replace('-', "_").to_uppercase());
        let mut candidates = Vec::<PathBuf>::new();
        if let Some(path) = std::env::var_os(&override_name) {
            candidates.push(path.into());
        } else if let Some(directory) = std::env::var_os("MIOS_NATIVE_BIN_DIR") {
            // An explicit catalog root is authoritative. Missing tools fail
            // here instead of silently executing an older host installation.
            candidates.push(PathBuf::from(directory).join(&filename));
        } else {
            if let Ok(executable) = std::env::current_exe() {
                if let Some(parent) = executable.parent() {
                    candidates.push(parent.join(&filename));
                }
                let mut cur = executable.parent();
                while let Some(dir) = cur {
                    if dir.join("tools/native").is_dir() {
                        candidates.push(dir.join("tools/native/target/debug").join(&filename));
                        candidates.push(dir.join("tools/native/target/release").join(&filename));
                        candidates.push(dir.join("src/mios-rs/target/debug").join(&filename));
                        candidates.push(dir.join("src/mios-rs/target/release").join(&filename));
                        break;
                    }
                    cur = dir.parent();
                }
            }
            for directory in [
                "tools/native/target/release",
                "tools/native/target/debug",
                "src/mios-rs/target/release",
                "src/mios-rs/target/debug",
                "usr/bin",
                "usr/libexec/mios",
            ] {
                candidates.push(ctx.root.join(directory).join(&filename));
            }
            candidates.push(Path::new("/usr/bin").join(&filename));
            candidates.push(Path::new("/usr/libexec/mios").join(&filename));
        }
        let binary = candidates
            .into_iter()
            .find(|p| p.is_file())
            .ok_or_else(|| {
                format!("required native {name} unavailable; install the SSOT release catalog")
            })?;
        let output = Command::new(binary)
            .args(args)
            .arg("--root")
            .arg(&ctx.root)
            .current_dir(&ctx.root)
            .env("MIOS_ROOT", &ctx.root)
            .env("MIOS_DRIFT_ROOT", &ctx.root)
            .env("MIOS_SSOT_LINT_ROOT", &ctx.root)
            .env("MIOS_TOML", ctx.root.join("usr/share/mios/mios.toml"))
            .env_remove("MIOS_DRIFT_CHECK_SOFT")
            .env_remove("MIOS_SSOT_LINT_SOFT")
            .output()
            .map_err(|e| format!("{name}: {e}"))?;
        let diagnostic = format!(
            "{}{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        if !output.status.success() {
            return Err(format!("{name} {args:?} {}: {diagnostic}", output.status));
        }
        Ok(format!(
            "native {name} {args:?} exit 0: {}",
            diagnostic.trim()
        ))
    })())
}

pub fn ini(text: &str, section: &str, key: &str) -> Option<String> {
    let mut active = false;
    let mut result = None;
    for line in text.lines().map(str::trim) {
        if line.starts_with('#') || line.starts_with(';') {
            continue;
        }
        if line.starts_with('[') {
            active = line == format!("[{section}]");
            continue;
        }
        if active {
            if let Some((k, v)) = line.split_once('=') {
                if k.trim() == key {
                    result = Some(v.trim().to_owned());
                }
            }
        }
    }
    result
}

#[derive(serde::Deserialize)]
pub struct PythonImport {
    pub module: Option<String>,
    pub names: Vec<String>,
    pub bindings: Vec<String>,
    pub level: usize,
    pub line: usize,
}

#[derive(serde::Deserialize)]
pub struct PythonSyntax {
    pub imports: Vec<PythonImport>,
    pub names: Vec<String>,
}

/// Obtain syntax nodes from the language's parser. Rust owns source selection,
/// policy and verdicts; importing/parsing a subject never executes that subject.
pub fn python_imports(ctx: &DriftCtx, path: &str) -> Result<Vec<PythonImport>, String> {
    Ok(python_syntax(ctx, path)?.imports)
}

pub fn python_syntax(ctx: &DriftCtx, path: &str) -> Result<PythonSyntax, String> {
    let config = ssot(ctx)?;
    let python = at(&config, "drift.lint.python")?
        .as_str()
        .filter(|s| !s.is_empty())
        .ok_or("Missing Python syntax compiler")?;
    let parser = "import ast,json,sys; tree=ast.parse(open(sys.argv[1],'rb').read(),sys.argv[1]); print(json.dumps({'imports':[{'module':getattr(n,'module',None),'names':[a.name for a in n.names],'bindings':[a.asname or a.name for a in n.names],'level':getattr(n,'level',0),'line':n.lineno} for n in ast.walk(tree) if isinstance(n,(ast.Import,ast.ImportFrom))],'names':[n.id for n in ast.walk(tree) if isinstance(n,ast.Name)]}))";
    let output = Command::new(python)
        .args(["-c", parser])
        .arg(ctx.root.join(path))
        .output()
        .map_err(|e| format!("Python syntax parser: {e}"))?;
    if !output.status.success() {
        return Err(format!(
            "{path}: Python AST {}: {}",
            output.status,
            String::from_utf8_lossy(&output.stderr)
        ));
    }
    serde_json::from_slice(&output.stdout)
        .map_err(|e| format!("{path}: Invalid Python AST receipt: {e}"))
}
