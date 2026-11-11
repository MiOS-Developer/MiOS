// AI-hint: DB-authoritative overlay hook -- inert by default unless MIOS_ACCOUNTS_DB_BACKED is enabled or --db-overlay is set.
// AI-related: usr/lib/mios/mios_toml.py, usr/lib/mios/mios_db_config.py
use crate::merge::{deep_merge, normalize_mons_layer};
use std::process::Command;
use toml::Value;

pub fn is_db_authoritative() -> bool {
    std::env::var("MIOS_ACCOUNTS_DB_BACKED")
        .map(|v| v == "true" || v == "1")
        .unwrap_or(false)
}

/// If DB mode is authoritative (or requested), shell out to mios_db_config and deep-merge the result.
pub fn maybe_apply_db_overlay(dst: &mut Value, force_db: bool) {
    if !force_db && !is_db_authoritative() {
        return;
    }

    let python_cmd = "import mios_db_config, json; cfg = mios_db_config.load_db_config() if mios_db_config.is_db_authoritative() else {}; print(json.dumps(cfg or {}))";

    let output = Command::new("python3").arg("-c").arg(python_cmd).output();

    if let Ok(out) = output {
        if out.status.success() {
            if let Ok(json_str) = String::from_utf8(out.stdout) {
                if let Ok(json_val) = serde_json::from_str::<serde_json::Value>(&json_str) {
                    if let Ok(toml_val) = json_to_toml(&json_val) {
                        apply_db_layer(dst, toml_val);
                    }
                }
            }
        }
    }
}

fn apply_db_layer(dst: &mut Value, mut layer: Value) {
    normalize_mons_layer(&mut layer);
    deep_merge(dst, layer);
}

fn json_to_toml(j: &serde_json::Value) -> Result<Value, ()> {
    match j {
        serde_json::Value::Null => Ok(Value::String("".into())),
        serde_json::Value::Bool(b) => Ok(Value::Boolean(*b)),
        serde_json::Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Ok(Value::Integer(i))
            } else if let Some(f) = n.as_f64() {
                Ok(Value::Float(f))
            } else {
                Err(())
            }
        }
        serde_json::Value::String(s) => Ok(Value::String(s.clone())),
        serde_json::Value::Array(arr) => {
            let mut vec = Vec::new();
            for item in arr {
                if let Ok(tv) = json_to_toml(item) {
                    vec.push(tv);
                }
            }
            Ok(Value::Array(vec))
        }
        serde_json::Value::Object(map) => {
            let mut table = toml::map::Map::new();
            for (k, v) in map {
                if let Ok(tv) = json_to_toml(v) {
                    table.insert(k.clone(), tv);
                }
            }
            Ok(Value::Table(table))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn raw_database_game_preferences_outrank_canonical_file_values() {
        let mut merged: Value = "[mons]\nzoom_percent=100\nrenderer='ascii'\n"
            .parse()
            .unwrap();
        let db = serde_json::json!({"tamagotchi":{"zoom_percent":125,"renderer":""},"mons":{"zoom_percent":150}});
        apply_db_layer(&mut merged, json_to_toml(&db).unwrap());
        assert_eq!(merged["mons"]["zoom_percent"].as_integer(), Some(150));
        assert_eq!(merged["mons"]["renderer"].as_str(), Some("ascii"));
        assert!(merged.get("tamagotchi").is_none());
        apply_db_layer(&mut merged, "tamagotchi=false".parse().unwrap());
        assert_eq!(merged["mons"].as_bool(), Some(false));
    }

    #[test]
    fn test_db_authoritative_default() {
        let prev = std::env::var("MIOS_ACCOUNTS_DB_BACKED");
        std::env::remove_var("MIOS_ACCOUNTS_DB_BACKED");
        let authoritative = is_db_authoritative();
        if let Ok(val) = prev {
            std::env::set_var("MIOS_ACCOUNTS_DB_BACKED", val);
        }
        assert!(!authoritative);
    }

    #[test]
    fn test_default_path_leaves_value_untouched() {
        let mut original: Value = toml::from_str(
            r#"
[identity]
role = "mini"
"#,
        )
        .unwrap();

        let expected = original.clone();
        maybe_apply_db_overlay(&mut original, false);
        assert_eq!(original, expected);
    }
}
