// AI-hint: Native Rust golden round-trip compiler for templates (ADR-0021, Law 14).
// AI-doc: usr/share/doc/mios/manual/tools.md
// AI-related: /usr/share/mios/templates/, /usr/share/mios/mios.toml, automation/98-drift-checks.sh

#![forbid(unsafe_code)]

use clap::Parser;
use std::collections::{BTreeMap, HashMap, HashSet};
use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

#[derive(Parser, Debug)]
#[command(
    name = "mios-template-compile",
    version,
    about = "Golden round-trip compiler for templates -- verifies all templates parse cleanly"
)]
pub struct Cli {
    /// Repository root directory
    #[arg(long)]
    pub root: Option<PathBuf>,

    /// Check mode: verify templates are valid (exits 0 on clean, 1 on failure)
    #[arg(long)]
    pub check: bool,

    /// Output format (text or json)
    #[arg(long, default_value = "text")]
    pub format: String,
}

pub fn resolve_root(cli_root: Option<&Path>) -> PathBuf {
    if let Some(r) = cli_root {
        return r.to_path_buf();
    }
    for var in &[
        "MIOS_DRIFT_ROOT",
        "MIOS_ROOT",
        "MIOS_THEME_ROOT",
        "MIOS_TOML_ROOT",
    ] {
        if let Ok(val) = env::var(var) {
            let trimmed = val.trim();
            if !trimmed.is_empty() {
                return PathBuf::from(trimmed);
            }
        }
    }
    PathBuf::from(".")
}

pub fn get_mock_vals(root_path: &Path) -> HashMap<String, String> {
    let mut map = HashMap::new();
    let config_path = root_path.join("usr/share/mios/mios.toml");

    if let Ok(content) = fs::read_to_string(&config_path) {
        if let Ok(val) = toml::from_str::<toml::Value>(&content) {
            if let Some(placeholders) = val
                .get("templates")
                .and_then(|t| t.get("placeholders"))
                .and_then(|p| p.as_table())
            {
                for (k, v) in placeholders {
                    if let Some(s) = v.as_str() {
                        map.insert(k.clone(), s.to_string());
                    }
                }
            }
        }
    }

    if map.is_empty() {
        map.insert("name".into(), "mockname".into());
        map.insert("PascalName".into(), "MockName".into());
        map.insert("date".into(), "2026-07-17".into());
        map.insert("id".into(), "9999".into());
        map.insert("title".into(), "Mock Title".into());
        map.insert("description".into(), "Mock Description".into());
        map.insert("status".into(), "proposed".into());
        map.insert("priority".into(), "P1".into());
        map.insert("theme".into(), "Mock Theme".into());
        map.insert("task_title".into(), "Mock Task Title".into());
        map.insert("task_id".into(), "8888".into());
        map.insert("image".into(), "mock-image:latest".into());
        map.insert("uid".into(), "1000".into());
        map.insert("gid".into(), "1000".into());
        map.insert("filename".into(), "mockname.py".into());
        map.insert(
            "path".into(),
            "usr/lib/mios/agent-pipe/mios_pipe/mockname.py".into(),
        );
    }

    map
}

pub fn get_registered_templates(root_path: &Path) -> Option<HashSet<String>> {
    let config_path = root_path.join("usr/share/mios/mios.toml");
    if let Ok(content) = fs::read_to_string(&config_path) {
        if let Ok(val) = toml::from_str::<toml::Value>(&content) {
            if let Some(templates_table) = val.get("templates").and_then(|t| t.as_table()) {
                let mut set = HashSet::new();
                for (k, _) in templates_table {
                    set.insert(k.clone());
                }
                return Some(set);
            }
        }
    }
    None
}

pub fn compile_template(
    name: &str,
    content: &str,
    mock_vals: &HashMap<String, String>,
) -> Option<String> {
    let mut rendered = content.to_string();
    for (k, v) in mock_vals {
        rendered = rendered.replace(&format!("{{{{{}}}}}", k), v);
    }

    match name {
        "json-schema" => {
            if let Err(e) = serde_json::from_str::<serde_json::Value>(&rendered) {
                return Some(format!("JSON Parse Error: {}", e));
            }
        }
        "toml-config" => {
            if let Err(e) = toml::from_str::<toml::Value>(&rendered) {
                return Some(format!("TOML Parse Error: {}", e));
            }
        }
        "yaml" => {
            if let Err(e) = serde_yaml::from_str::<serde_yaml::Value>(&rendered) {
                return Some(format!("YAML Parse Error: {}", e));
            }
        }
        "python-module" | "python-test" | "python-tool" => {
            let candidates = if cfg!(target_os = "windows") {
                vec!["python", "py", "python3"]
            } else {
                vec!["python3", "python"]
            };

            for cmd in candidates {
                if let Ok(mut child) = Command::new(cmd)
                    .arg("-c")
                    .arg("import sys; compile(sys.stdin.read(), 'src', 'exec')")
                    .stdin(std::process::Stdio::piped())
                    .stderr(std::process::Stdio::piped())
                    .stdout(std::process::Stdio::null())
                    .spawn()
                {
                    use std::io::Write;
                    if let Some(mut stdin) = child.stdin.take() {
                        let _ = stdin.write_all(rendered.as_bytes());
                    }
                    if let Ok(output) = child.wait_with_output() {
                        if !output.status.success() {
                            let err_msg =
                                String::from_utf8_lossy(&output.stderr).trim().to_string();
                            return Some(format!("Python SyntaxError: {}", err_msg));
                        }
                        break;
                    }
                }
            }
        }
        "bash" | "bash-verb" | "drift-check" | "automation-step" => {
            if let Ok(mut child) = Command::new("bash")
                .arg("-n")
                .stdin(std::process::Stdio::piped())
                .stderr(std::process::Stdio::piped())
                .stdout(std::process::Stdio::null())
                .spawn()
            {
                use std::io::Write;
                if let Some(mut stdin) = child.stdin.take() {
                    let _ = stdin.write_all(rendered.as_bytes());
                }
                if let Ok(output) = child.wait_with_output() {
                    if !output.status.success() {
                        let err_msg = String::from_utf8_lossy(&output.stderr).trim().to_string();
                        return Some(format!("Bash syntax check failed: {}", err_msg));
                    }
                }
            }
        }
        _ => {}
    }

    None
}

pub fn execute(cli: &Cli) -> (i32, Option<String>, Option<String>) {
    let root_path = resolve_root(cli.root.as_deref());
    let templates_dir = root_path.join("usr/share/mios/templates");

    if !templates_dir.is_dir() {
        if cli.format == "json" {
            let err_json = serde_json::json!({
                "status": "error",
                "subcommand": "compile-templates",
                "target": "usr/share/mios/templates",
                "error": format!("Templates directory not found: {:?}", templates_dir)
            });
            return (1, None, Some(err_json.to_string()));
        } else {
            return (
                1,
                None,
                Some(format!(
                    "[compile-templates] Templates directory not found: {:?}",
                    templates_dir
                )),
            );
        }
    }

    let mock_vals = get_mock_vals(&root_path);
    let registered_templates = get_registered_templates(&root_path);
    let mut failures: BTreeMap<String, String> = BTreeMap::new();
    let mut success_count = 0;

    let entries = match fs::read_dir(&templates_dir) {
        Ok(e) => e,
        Err(err) => {
            let msg = format!("[compile-templates] Read dir error: {}", err);
            return (1, None, Some(msg));
        }
    };

    let mut names: Vec<String> = Vec::new();
    for entry in entries.flatten() {
        let fn_str = entry.file_name().to_string_lossy().to_string();
        if entry.path().is_dir()
            || fn_str.starts_with('.')
            || fn_str == "conformance-grandfathered.list"
            || fn_str == "__pycache__"
        {
            continue;
        }
        names.push(fn_str);
    }
    names.sort();

    for fn_str in &names {
        if let Some(ref registered) = registered_templates {
            if !registered.contains(fn_str) {
                failures.insert(
                    fn_str.clone(),
                    "Not registered in mios.toml [templates.*]".to_string(),
                );
                continue;
            }
        }

        let path = templates_dir.join(fn_str);
        let content = match fs::read_to_string(&path) {
            Ok(c) => c,
            Err(e) => {
                failures.insert(fn_str.clone(), format!("Read error: {}", e));
                continue;
            }
        };

        if let Some(err) = compile_template(fn_str, &content, &mock_vals) {
            failures.insert(fn_str.clone(), err);
        } else {
            success_count += 1;
        }
    }

    let total_templates = success_count + failures.len();

    if cli.format == "json" {
        if failures.is_empty() {
            let clean_json = serde_json::json!({
                "status": "clean",
                "subcommand": "compile-templates",
                "target": "usr/share/mios/templates",
                "templates_count": total_templates,
                "violations": 0
            });
            (0, Some(clean_json.to_string()), None)
        } else {
            let drift_json = serde_json::json!({
                "status": "drift",
                "subcommand": "compile-templates",
                "target": "usr/share/mios/templates",
                "templates_count": total_templates,
                "violations": failures.len(),
                "failures": failures
            });
            (1, None, Some(drift_json.to_string()))
        }
    } else if failures.is_empty() {
        let msg = format!(
            "[compile-templates] PASS: All {} templates compiled/validated successfully.",
            success_count
        );
        (0, Some(msg), None)
    } else {
        let mut err_msg = format!(
            "[compile-templates] FAIL: {} template(s) failed compilation/validation:\n",
            failures.len()
        );
        for (fn_str, err) in &failures {
            err_msg.push_str(&format!("    {}: {}\n", fn_str, err));
        }
        (1, None, Some(err_msg.trim_end().to_string()))
    }
}

fn main() {
    let cli = Cli::parse();
    let (code, stdout, stderr) = execute(&cli);
    if let Some(out) = stdout {
        println!("{}", out);
    }
    if let Some(err) = stderr {
        eprintln!("{}", err);
    }
    std::process::exit(code);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_get_mock_vals() {
        let root = Path::new("/nonexistent_dir_12345");
        let mocks = get_mock_vals(root);
        assert_eq!(mocks.get("name").unwrap(), "mockname");
        assert_eq!(mocks.get("PascalName").unwrap(), "MockName");
    }

    #[test]
    fn test_compile_template_json() {
        let mut mocks = HashMap::new();
        mocks.insert("name".to_string(), "foo".to_string());
        let content = r#"{"key": "{{name}}"}"#;
        let err = compile_template("json-schema", content, &mocks);
        assert!(err.is_none());

        let invalid = r#"{"key": "{{name}}""#;
        let err_inv = compile_template("json-schema", invalid, &mocks);
        assert!(err_inv.is_some());
    }

    #[test]
    fn test_compile_template_toml() {
        let mut mocks = HashMap::new();
        mocks.insert("name".to_string(), "bar".to_string());
        let content = "key = \"{{name}}\"\n";
        let err = compile_template("toml-config", content, &mocks);
        assert!(err.is_none());

        let invalid = "key = \n";
        let err_inv = compile_template("toml-config", invalid, &mocks);
        assert!(err_inv.is_some());
    }

    #[test]
    fn test_compile_template_yaml() {
        let mut mocks = HashMap::new();
        mocks.insert("name".to_string(), "baz".to_string());
        let content = "name: {{name}}\n";
        let err = compile_template("yaml", content, &mocks);
        assert!(err.is_none());

        let invalid = ": invalid: yaml: [";
        let err_inv = compile_template("yaml", invalid, &mocks);
        assert!(err_inv.is_some());
    }
}
