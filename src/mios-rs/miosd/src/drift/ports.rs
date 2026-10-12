// AI-hint: Port literal and container ports checks for miosd drift runner.
// AI-related: mios.toml [ports] [docs].retired_ports [docs].retired_code_exemptions, usr/share/containers/systemd, usr/lib/mios/agent-pipe, usr/libexec/mios, usr/bin

use super::audit;
use super::{Check, DriftCtx, Verdict};
use regex::Regex;
use std::collections::BTreeSet;
use std::io::Write;
use std::process::{Command, Stdio};

pub struct ContainerPortsCheck;
impl Check for ContainerPortsCheck {
    fn id(&self) -> &'static str {
        "check_container_ports"
    }
    fn describe(&self) -> &'static str {
        "Assert container port mappings match SSOT [ports] definitions"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        audit::verdict(container_ports(ctx))
    }
}

pub struct BarePortLiteralsCheck;
impl Check for BarePortLiteralsCheck {
    fn id(&self) -> &'static str {
        "check_no_bare_port_literals"
    }
    fn describe(&self) -> &'static str {
        "Assert no bare port number literals exist outside SSOT"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        audit::verdict(bare_port_literals(ctx))
    }
}

const QUADLET_DIRS: [&str; 2] = ["usr/share/containers/systemd", "etc/containers/systemd"];

/// Every active line of every `.container` Quadlet is searched for each SSOT
/// `[ports]` value. A value is SSOT-sourced only inside `${MIOS_PORT(S)_*:-N}`;
/// anywhere else it is a hand-copied literal that a retuned `[ports]` misses.
fn container_ports(ctx: &DriftCtx) -> audit::Audit {
    let config = audit::ssot(ctx)?;
    let table = audit::at(&config, "ports")?
        .as_table()
        .ok_or("SSOT ports must be a table")?;
    let ports: Vec<(&str, i64)> = table
        .iter()
        .filter(|(name, _)| name.as_str() != "stack_id")
        .filter_map(|(name, value)| value.as_integer().map(|port| (name.as_str(), port)))
        .collect();
    if ports.is_empty() {
        return Err(
            "SSOT [ports] declares no integer port, so no Quadlet literal can be recognised".into(),
        );
    }
    // Container-side listening ports. The legacy check hard-coded (8080, 3002),
    // the SearXNG and firecrawl upstream internals; SSOT now names that class
    // `*_internal`. They may appear as the container side of a mapping or in an
    // in-container `X=N`, never as a bare host-side `PublishPort=N`.
    let internal: BTreeSet<i64> = ports
        .iter()
        .filter(|(name, _)| name.ends_with("_internal"))
        .map(|(_, port)| *port)
        .collect();
    let mut patterns = Vec::with_capacity(ports.len());
    for (name, port) in &ports {
        // MIOS_PORTS_<KEY> is the canonical [ports] env name; the legacy regex
        // knew only the older MIOS_PORT_ prefix (still used by guacamole), so it
        // flagged every SSOT-wired fallback in the tree.
        let fallback = Regex::new(&format!(r"\$\{{MIOS_PORTS?_[A-Z0-9_]+:-{port}\}}"))
            .map_err(|e| e.to_string())?;
        let literal = Regex::new(&format!(r"\b{port}\b")).map_err(|e| e.to_string())?;
        patterns.push((*name, *port, fallback, literal));
    }
    let mut subjects = 0;
    let mut errors = Vec::new();
    for dir in QUADLET_DIRS {
        if !ctx.root.join(dir).is_dir() {
            continue;
        }
        for path in audit::files(&ctx.root, dir)? {
            if !path.ends_with(".container") {
                continue;
            }
            subjects += 1;
            let text = audit::read(&ctx.root, &path)?;
            for (index, line) in text.lines().enumerate() {
                // systemd unit syntax: only a whole line starting with # or ; is a
                // comment. The legacy `#.*` strip also cut active values at a
                // mid-line # (URL fragments, `$#`), hiding literals that execute.
                let active = line.trim();
                if active.is_empty() || active.starts_with('#') || active.starts_with(';') {
                    continue;
                }
                for (name, port, fallback, literal) in &patterns {
                    let cleaned = fallback.replace_all(active, "");
                    if !literal.is_match(&cleaned) {
                        continue;
                    }
                    let container_side = cleaned.contains(&format!(":{port}"))
                        || (cleaned.contains(&format!("={port}"))
                            && !cleaned.starts_with("PublishPort="));
                    if internal.contains(port) && container_side {
                        continue;
                    }
                    errors.push(format!(
                        "{path}:{}: manual port literal {port} for [ports].{name}; write ${{MIOS_PORTS_{}:-{port}}} so the SSOT value reaches the unit: {active}",
                        index + 1,
                        name.to_uppercase()
                    ));
                }
            }
        }
    }
    audit::finish(
        subjects,
        errors,
        "Quadlet .container files scanned for hand-copied [ports] literals",
    )
}

const EXECUTION_PATHS: [&str; 3] = ["usr/lib/mios/agent-pipe", "usr/libexec/mios", "usr/bin"];
/// Legacy corpus floor: fewer subjects means the root is not a MiOS tree, so a
/// clean result would certify nothing. No SSOT key carries it yet.
const MIN_EXECUTION_PATH_FILES: usize = 100;

/// Retired local-lane ports (Law 5) must not survive in execution-path code.
/// The roster is the SSOT registry `[docs].retired_ports` and the exemptions are
/// the itemised `[docs].retired_code_exemptions` paths (exact match), replacing
/// the legacy hard-coded six ports and eleven basenames. Docstrings, comments
/// and echo/usage text are prose, not execution, and stay exempt as before.
fn bare_port_literals(ctx: &DriftCtx) -> audit::Audit {
    let config = audit::ssot(ctx)?;
    let retired = audit::at(&config, "docs.retired_ports")?
        .as_array()
        .ok_or("SSOT docs.retired_ports must be an array")?
        .iter()
        .map(|port| {
            port.as_integer()
                .filter(|port| *port > 0)
                .map(|port| port.to_string())
                .ok_or("SSOT docs.retired_ports contains a non-port entry")
        })
        .collect::<Result<Vec<_>, _>>()?;
    if retired.is_empty() {
        return Err("SSOT docs.retired_ports is empty, so no retired port can be detected".into());
    }
    let exempt: BTreeSet<String> = audit::strings(&config, "docs.retired_code_exemptions")?
        .into_iter()
        .collect();
    let python = audit::at(&config, "drift.lint.python")?
        .as_str()
        .filter(|s| !s.trim().is_empty())
        .ok_or("Invalid SSOT drift.lint.python")?;
    let absent: Vec<_> = EXECUTION_PATHS
        .iter()
        .filter(|dir| !ctx.root.join(dir).is_dir())
        .map(|dir| format!("{dir}: execution-path root is absent, so nothing there was scanned"))
        .collect();
    if !absent.is_empty() {
        return Err(absent.join("\n"));
    }
    let mut subjects = Vec::new();
    for dir in EXECUTION_PATHS {
        for path in audit::files(&ctx.root, dir)? {
            let name = path.rsplit('/').next().unwrap_or_default();
            // Anchored: the legacy `"test_" in name` also skipped e.g. latest_x.py.
            let source = [".py", ".sh", ".ps1"].iter().any(|ext| name.ends_with(ext));
            if source && !name.starts_with("test_") && !exempt.contains(&path) {
                subjects.push(path);
            }
        }
    }
    if subjects.len() < MIN_EXECUTION_PATH_FILES {
        return Err(format!(
            "only {} execution-path file(s) under {} -- the corpus is wrong, so an empty result is not a pass",
            subjects.len(),
            EXECUTION_PATHS.join(", ")
        ));
    }
    let python_subjects: Vec<&str> = subjects
        .iter()
        .map(String::as_str)
        .filter(|p| p.ends_with(".py"))
        .collect();
    let mut parsed = python_constants(python, &ctx.root, &python_subjects)?.into_iter();
    let mut errors = BTreeSet::new();
    for path in &subjects {
        let bytes = std::fs::read(ctx.root.join(path)).map_err(|e| format!("{path}: {e}"))?;
        let text = String::from_utf8_lossy(&bytes);
        let constants = if path.ends_with(".py") {
            parsed
                .next()
                .ok_or_else(|| format!("{path}: Python AST receipt is missing"))?
                .constants
        } else {
            None
        };
        if let Some(constants) = constants {
            for (line, value) in constants {
                for port in retired_in(&value, &retired) {
                    let shown: String = value.chars().take(80).collect();
                    errors.insert(format!("{path}:{line}: retired port {port} in constant {shown:?}; read its [ports] successor key instead"));
                }
            }
            continue;
        }
        // Shell/PowerShell, or Python that does not parse (legacy text fallback).
        let prose: &[&str] = if path.ends_with(".py") {
            &["#", "'''", "\"\"\""]
        } else {
            &["#", "//", "Write-Host", "echo", "help", "usage"]
        };
        for (index, line) in text.lines().enumerate() {
            if prose.iter().any(|prefix| line.trim().starts_with(prefix)) {
                continue;
            }
            // Only # opens a comment here; splitting on // too hid http://host:PORT.
            let code = line.split('#').next().unwrap_or_default();
            for port in retired_in(code, &retired) {
                errors.insert(format!("{path}:{}: retired port {port} in code; read its [ports] successor key instead", index + 1));
            }
        }
    }
    let mut errors: Vec<_> = errors.into_iter().collect();
    if !errors.is_empty() {
        errors.push("fix: replace each with its [ports] key, or register a cleanup/fixture file in [docs].retired_code_exemptions".into());
    }
    audit::finish(
        subjects.len(),
        errors,
        &format!(
            "execution-path file(s) scanned for {} retired port(s)",
            retired.len()
        ),
    )
}

/// Ports from `retired` occurring in `text` as a whole number. Anchored on digit
/// boundaries: a substring test would also fire on 114340 or a 5-digit suffix.
fn retired_in<'a>(text: &str, retired: &'a [String]) -> Vec<&'a str> {
    retired
        .iter()
        .filter(|port| {
            text.match_indices(port.as_str()).any(|(at, _)| {
                let before = text[..at].chars().next_back();
                let after = text[at + port.len()..].chars().next();
                !before.is_some_and(|c| c.is_ascii_digit())
                    && !after.is_some_and(|c| c.is_ascii_digit())
            })
        })
        .map(String::as_str)
        .collect()
}

#[derive(serde::Deserialize)]
struct PythonConstants {
    /// None when the file does not parse; the caller then text-scans it.
    constants: Option<Vec<(usize, String)>>,
}

/// One isolated interpreter (`-I -B`, temp cwd) parses every file and returns,
/// per file in request order, its non-docstring constants that contain a digit.
/// Python only parses; which ports count, and the verdict, stay in Rust.
fn python_constants(
    python: &str,
    root: &std::path::Path,
    paths: &[&str],
) -> Result<Vec<PythonConstants>, String> {
    if paths.is_empty() {
        return Ok(Vec::new());
    }
    let parser = "import ast,json,sys\nout=[]\nfor p in sys.stdin.buffer.read().decode().split('\\0'):\n    try:\n        t=ast.parse(open(p,'rb').read().decode('utf-8','ignore'),p)\n    except (SyntaxError,ValueError):\n        out.append({'constants':None});continue\n    d={id(n.body[0].value) for n in ast.walk(t) if isinstance(n,(ast.Module,ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)) and n.body and isinstance(n.body[0],ast.Expr) and isinstance(n.body[0].value,ast.Constant) and isinstance(n.body[0].value.value,str)}\n    out.append({'constants':[[n.lineno,str(n.value)] for n in ast.walk(t) if isinstance(n,ast.Constant) and id(n) not in d and any(c.isdigit() for c in str(n.value))]})\nprint(json.dumps(out))\n";
    let request = paths
        .iter()
        .map(|path| root.join(path).to_string_lossy().into_owned())
        .collect::<Vec<_>>()
        .join("\0");
    let mut child = Command::new(python)
        .args(["-I", "-B", "-c", parser])
        .current_dir(std::env::temp_dir())
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("Python syntax parser {python}: {e}"))?;
    child
        .stdin
        .take()
        .ok_or("Python syntax parser stdin unavailable")?
        .write_all(request.as_bytes())
        .map_err(|e| format!("Python syntax parser request: {e}"))?;
    let output = child
        .wait_with_output()
        .map_err(|e| format!("Python syntax parser: {e}"))?;
    if !output.status.success() {
        return Err(format!(
            "Python AST {}: {}",
            output.status,
            String::from_utf8_lossy(&output.stderr)
        ));
    }
    let parsed: Vec<PythonConstants> = serde_json::from_slice(&output.stdout)
        .map_err(|e| format!("Invalid Python AST receipt: {e}"))?;
    if parsed.len() != paths.len() {
        return Err(format!(
            "Python AST receipt covers {} of {} file(s)",
            parsed.len(),
            paths.len()
        ));
    }
    Ok(parsed)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::path::Path;

    fn write(root: &Path, path: &str, text: &str) -> Result<(), Box<dyn std::error::Error>> {
        let path = root.join(path);
        fs::create_dir_all(path.parent().ok_or("no parent")?)?;
        fs::write(path, text)?;
        Ok(())
    }

    const PORTS: &str = "[ports]\nstack_id = 0\nforge_http = 8400\nsearxng = 8800\nsearxng_internal = 8080\nunbound = []\n[ports.categories.forge]\nbase = 8400\n";

    #[test]
    fn quadlet_literals_fail_and_ssot_fallbacks_internal_ports_and_comments_pass(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let temp = tempfile::tempdir()?;
        let root = temp.path();
        let ctx = DriftCtx::new(root.into(), false);
        assert!(container_ports(&ctx).is_err_and(|e| e.contains("mios.toml")));
        write(root, "usr/share/mios/mios.toml", PORTS)?;
        assert!(container_ports(&ctx).is_err_and(|e| e.contains("no subjects")));
        let unit = "usr/share/containers/systemd/mios-forge.container";
        let clean = "[Container]\n# PublishPort=8400:8400 is documentation\n; 8800 too\nEnvironment=HTTP_PORT=${MIOS_PORTS_FORGE_HTTP:-8400}\nLabel=url=http://localhost:${MIOS_PORT_FORGE_HTTP:-8400}/\nPublishPort=127.0.0.1:${MIOS_PORTS_SEARXNG:-8800}:8080\nEnvironment=PORT=8080\nExec=serve --threads 84000\n";
        write(root, unit, clean)?;
        write(
            root,
            "usr/share/containers/systemd/mios-forge.image",
            "Image=x:8400\n",
        )?;
        assert!(container_ports(&ctx)?.contains(": 1 subject"));
        write(root, unit, &format!("{clean}PublishPort=8400:8400\n"))?;
        assert!(container_ports(&ctx).is_err_and(|e| e.contains(&format!(
            "{unit}:9: manual port literal 8400 for [ports].forge_http"
        ))));
        write(root, unit, &format!("{clean}PublishPort=8080\n"))?;
        assert!(container_ports(&ctx).is_err_and(
            |e| e.contains(":9: manual port literal 8080 for [ports].searxng_internal")
        ));
        write(
            root,
            unit,
            &format!("{clean}Label=x=http://h/#/${{MIOS_PORTS_X:-1}}:8800\n"),
        )?;
        assert!(container_ports(&ctx)
            .is_err_and(|e| e.contains(":9: manual port literal 8800 for [ports].searxng;")));
        write(root, "usr/share/mios/mios.toml", "[ports]\nstack_id = 0\n")?;
        assert!(container_ports(&ctx).is_err_and(|e| e.contains("no integer port")));
        Ok(())
    }

    const DOCS: &str = "[docs]\nretired_ports = [11434, 3030]\nretired_code_exemptions = [\"usr/libexec/mios/Cleanup.ps1\"]\n[drift.lint]\npython = 'python3'\n";

    fn corpus(root: &Path) -> Result<(), Box<dyn std::error::Error>> {
        write(root, "usr/share/mios/mios.toml", DOCS)?;
        for index in 0..MIN_EXECUTION_PATH_FILES {
            write(
                root,
                &format!("usr/bin/tool{index}.sh"),
                "#!/bin/sh\nexec mios \"$@\"\n",
            )?;
        }
        write(root, "usr/lib/mios/agent-pipe/mios_lane.py", "\"\"\"Was :11434 before the cutover.\"\"\"\nimport os\n# localhost:11434\nPORT = os.environ['MIOS_PORTS_LLM_LIGHT']\nSIZE = 114340\n")?;
        write(
            root,
            "usr/lib/mios/agent-pipe/test_mios_lane.py",
            "URL = 'http://h:11434/v1'\n",
        )?;
        write(
            root,
            "usr/libexec/mios/Cleanup.ps1",
            "netsh delete port 3030\n",
        )?;
        write(
            root,
            "usr/libexec/mios/mios-verb",
            "curl http://localhost:11434/\n",
        )?;
        write(
            root,
            "usr/libexec/mios/notes.sh",
            "echo 'was :3030'\n# :11434\n",
        )?;
        Ok(())
    }

    #[test]
    fn retired_ports_in_code_fail_while_prose_tests_and_register_pass(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let temp = tempfile::tempdir()?;
        let root = temp.path();
        let ctx = DriftCtx::new(root.into(), false);
        corpus(root)?;
        let pass = bare_port_literals(&ctx)?;
        assert!(pass.contains("2 retired port(s): 102 subject"), "{pass}");
        write(
            root,
            "usr/share/mios/mios.toml",
            &DOCS.replace("\"usr/libexec/mios/Cleanup.ps1\"", ""),
        )?;
        assert!(bare_port_literals(&ctx).is_err_and(
            |e| e.contains("usr/libexec/mios/Cleanup.ps1:1: retired port 3030 in code")
        ));
        corpus(root)?;
        write(root, "usr/lib/mios/agent-pipe/mios_lane.py", "def lane():\n    \"\"\"Docstring :11434.\"\"\"\n    return 'http://127.0.0.1:11434/v1'\n")?;
        assert!(bare_port_literals(&ctx).is_err_and(|e| e
            .contains("usr/lib/mios/agent-pipe/mios_lane.py:3: retired port 11434 in constant")));
        write(
            root,
            "usr/lib/mios/agent-pipe/mios_lane.py",
            "def broken(:\n    URL = 'http://h:3030/' # 11434\n",
        )?;
        let parse_fallback = bare_port_literals(&ctx).err().unwrap_or_default();
        assert!(
            parse_fallback.contains("mios_lane.py:2: retired port 3030 in code")
                && !parse_fallback.contains("11434"),
            "{parse_fallback}"
        );
        corpus(root)?;
        write(
            root,
            "usr/libexec/mios/Lan.ps1",
            "$p = 'http://localhost:3030/' // not a comment\n",
        )?;
        assert!(bare_port_literals(&ctx)
            .is_err_and(|e| e.contains("usr/libexec/mios/Lan.ps1:1: retired port 3030 in code")));
        fs::remove_file(root.join("usr/libexec/mios/Lan.ps1"))?;
        write(root, "usr/libexec/mios/latest_ports.sh", "PORT=11434\n")?;
        assert!(bare_port_literals(&ctx)
            .is_err_and(|e| e.contains("latest_ports.sh:1: retired port 11434")));
        fs::remove_file(root.join("usr/libexec/mios/latest_ports.sh"))?;
        fs::remove_dir_all(root.join("usr/bin"))?;
        assert!(bare_port_literals(&ctx)
            .is_err_and(|e| e.contains("usr/bin: execution-path root is absent")));
        fs::create_dir_all(root.join("usr/bin"))?;
        assert!(
            bare_port_literals(&ctx).is_err_and(|e| e.contains("only 2 execution-path file(s)"))
        );
        corpus(root)?;
        write(
            root,
            "usr/share/mios/mios.toml",
            &DOCS.replace("[11434, 3030]", "[]"),
        )?;
        assert!(bare_port_literals(&ctx).is_err_and(|e| e.contains("retired_ports is empty")));
        Ok(())
    }

    #[test]
    fn retired_port_match_is_digit_anchored() {
        let retired = vec!["11434".to_owned()];
        assert_eq!(retired_in("http://h:11434/v1", &retired), vec!["11434"]);
        assert!(retired_in("114340 and 211434", &retired).is_empty());
        assert_eq!(retired_in("211434 then 11434", &retired), vec!["11434"]);
    }
}
