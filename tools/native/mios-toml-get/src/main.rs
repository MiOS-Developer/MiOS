// AI-hint: Fast native static CLI for querying the layered mios.toml SSOT.
// AI-related: usr/libexec/mios/mios-toml-get, usr/lib/mios/mios_toml.py
// AI-functions: main, get_section, format_scalar, run

use std::env;
use std::fs;
use std::path::Path;
use std::process::ExitCode;
use toml::Value;

const USAGE: &str = "usage: mios-toml-get [--vendor] <section[.sub]> <key> [default]   # scalar\n       mios-toml-get [--vendor] --section <a.b>                    # sub-table as JSON\n       mios-toml-get [--vendor] --dump <a.b> <key> [<key> ...]     # k=v lines\n";

fn get_section<'a>(data: &'a Value, path: &str) -> Option<&'a toml::Table> {
    let mut current = data;
    for part in path.split('.') {
        match current {
            Value::Table(t) => {
                current = t.get(part)?;
            }
            _ => return None,
        }
    }
    current.as_table()
}

fn format_scalar(v: Option<&Value>) -> String {
    match v {
        None => String::new(),
        Some(Value::Boolean(b)) => {
            if *b {
                "true".to_string()
            } else {
                "false".to_string()
            }
        }
        Some(Value::String(s)) => s.clone(),
        Some(Value::Integer(i)) => i.to_string(),
        Some(Value::Float(f)) => f.to_string(),
        Some(Value::Datetime(dt)) => dt.to_string(),
        Some(Value::Array(_) | Value::Table(_)) => {
            serde_json::to_string(v.unwrap()).unwrap_or_default()
        }
    }
}

fn load_vendor_data(root: Option<&Path>) -> Result<Value, String> {
    let (vendor_path, _, _, _, _, _) = mios_resolver::layers::resolve_tier_dirs(root);
    if vendor_path.is_file() {
        let text = fs::read_to_string(&vendor_path)
            .map_err(|e| format!("cannot read {}: {}", vendor_path.display(), e))?;
        text.parse::<Value>()
            .map_err(|e| format!("cannot parse {}: {}", vendor_path.display(), e))
    } else {
        Ok(Value::Table(toml::Table::new()))
    }
}

pub fn run(args: &[String]) -> Result<String, (String, u8)> {
    if args.is_empty() || args[0] == "-h" || args[0] == "--help" {
        return Err((USAGE.to_string(), 2));
    }

    let mut use_vendor = false;
    let mut rest = args;

    if rest[0] == "--vendor" {
        use_vendor = true;
        rest = &rest[1..];
    }

    if rest.is_empty() {
        return Err((
            "mios-toml-get: missing arguments after --vendor\n".to_string(),
            2,
        ));
    }

    let root_env = env::var("MIOS_TOML_ROOT").ok();
    let root_path = root_env.as_ref().map(Path::new);

    let data = if use_vendor {
        load_vendor_data(root_path).unwrap_or_else(|_| Value::Table(toml::Table::new()))
    } else {
        mios_resolver::resolve_merged(root_path, false)
            .unwrap_or_else(|_| Value::Table(toml::Table::new()))
    };

    if rest[0] == "--section" {
        if rest.len() < 2 {
            return Err(("mios-toml-get --section needs a section\n".to_string(), 2));
        }
        let sect = get_section(&data, &rest[1]);
        let json_str = match sect {
            Some(table) => serde_json::to_string(table).unwrap_or_else(|_| "{}".to_string()),
            None => "{}".to_string(),
        };
        return Ok(json_str);
    }

    if rest[0] == "--dump" {
        if rest.len() < 3 {
            return Err((
                "mios-toml-get --dump needs a section + at least one key\n".to_string(),
                2,
            ));
        }
        let sect = get_section(&data, &rest[1]);
        let mut lines = Vec::new();
        for key in &rest[2..] {
            let val = sect.and_then(|t| t.get(key));
            lines.push(format!("{}={}", key, format_scalar(val)));
        }
        return Ok(lines.join("\n"));
    }

    if rest.len() < 2 {
        return Err(("mios-toml-get needs <section> <key>\n".to_string(), 2));
    }

    let section_name = &rest[0];
    let key_name = &rest[1];
    let default_val = if rest.len() > 2 { rest[2].as_str() } else { "" };

    let sect = get_section(&data, section_name);
    let val = sect.and_then(|t| t.get(key_name));

    match val {
        Some(v) => Ok(format_scalar(Some(v))),
        None => Ok(default_val.to_string()),
    }
}

fn main() -> ExitCode {
    let args: Vec<String> = env::args().skip(1).collect();
    match run(&args) {
        Ok(output) => {
            println!("{}", output);
            ExitCode::from(0)
        }
        Err((err_msg, code)) => {
            eprint!("{}", err_msg);
            ExitCode::from(code)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_format_scalar() {
        assert_eq!(format_scalar(None), "");
        assert_eq!(format_scalar(Some(&Value::Boolean(true))), "true");
        assert_eq!(format_scalar(Some(&Value::Boolean(false))), "false");
        assert_eq!(format_scalar(Some(&Value::Integer(42))), "42");
        assert_eq!(
            format_scalar(Some(&Value::String("hello".to_string()))),
            "hello"
        );
    }

    #[test]
    fn test_get_section_and_values() {
        let toml_str = r#"
            [meta]
            mios_version = "0.3.0"
            debug = false

            [ports]
            llm_light = 8500
            services = ["a", "b"]

            [nested.sub]
            target = "seat"
        "#;
        let data: Value = toml_str.parse().unwrap();

        let sec = get_section(&data, "meta").unwrap();
        assert_eq!(sec.get("mios_version").unwrap().as_str().unwrap(), "0.3.0");

        let nested_sec = get_section(&data, "nested.sub").unwrap();
        assert_eq!(nested_sec.get("target").unwrap().as_str().unwrap(), "seat");

        assert!(get_section(&data, "absent").is_none());
    }

    #[test]
    fn test_run_help() {
        let (msg, code) = run(&["--help".to_string()]).unwrap_err();
        assert_eq!(code, 2);
        assert!(msg.starts_with("usage: mios-toml-get"));
    }

    #[test]
    fn test_run_missing_args() {
        let (msg, code) = run(&["meta".to_string()]).unwrap_err();
        assert_eq!(code, 2);
        assert!(msg.contains("needs <section> <key>"));
    }

    #[test]
    fn test_run_dump_missing_args() {
        let (msg, code) = run(&["--dump".to_string(), "meta".to_string()]).unwrap_err();
        assert_eq!(code, 2);
        assert!(msg.contains("--dump needs a section + at least one key"));
    }
}
