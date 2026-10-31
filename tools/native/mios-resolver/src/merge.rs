// AI-hint: Rust module for merging TOML trees according to MiOS overlay semantics.
use toml::Value;

/// Recursively merge `src` into `dst` following MiOS overlay semantics:
/// - Nested tables are recursively merged.
/// - An empty string in `src` NEVER overrides a non-empty string in `dst`.
/// - Lists and non-empty scalars in `src` overwrite values in `dst`.
pub fn deep_merge(dst: &mut Value, src: Value) {
    match (dst, src) {
        (Value::Table(dst_table), Value::Table(src_table)) => {
            deep_merge_table(dst_table, src_table);
        }
        (dst_val, src_val) => {
            if is_empty_string_shadow(dst_val, &src_val) {
                return;
            }
            *dst_val = src_val;
        }
    }
}

/// The part of `new` a higher tier must carry to turn `base` into `new`:
/// keys absent from `base` or holding a different value, recursing into
/// tables and dropping empty ones. None when nothing differs. Mirrors
/// mios_pipe.kernel.config.write_user_config's _dict_diff.
pub fn diff_against(new: &Value, base: &Value) -> Option<Value> {
    match (new, base) {
        (Value::Table(n), Value::Table(b)) => {
            let mut out = toml::Table::new();
            for (k, v) in n {
                match b.get(k) {
                    None => {
                        out.insert(k.clone(), v.clone());
                    }
                    Some(bv) => {
                        if let Some(d) = diff_against(v, bv) {
                            out.insert(k.clone(), d);
                        }
                    }
                }
            }
            (!out.is_empty()).then_some(Value::Table(out))
        }
        (n, b) if n == b => None,
        (n, _) => Some(n.clone()),
    }
}

pub fn deep_merge_table(dst: &mut toml::Table, src: toml::Table) {
    for (k, v) in src {
        match dst.get_mut(&k) {
            Some(dst_v) => {
                if dst_v.is_table() && v.is_table() {
                    deep_merge(dst_v, v);
                } else if is_empty_string_shadow(dst_v, &v) {
                    continue;
                } else {
                    dst.insert(k, v);
                }
            }
            None => {
                dst.insert(k, v);
            }
        }
    }
}

/// An empty string never overrides a value below it -- ANY value but another
/// empty string, as mios_toml.deep_merge reads the rule. Shadowing only a
/// non-empty STRING let `port = ""` in a user tier blank an integer port, a
/// boolean or a table for every native reader while mios_toml.py kept it
/// (Law 13; tests/test-resolver-dropin-parity.py).
fn is_empty_string_shadow(dst_val: &Value, src_val: &Value) -> bool {
    matches!(src_val, Value::String(s) if s.is_empty())
        && !matches!(dst_val, Value::String(s) if s.is_empty())
}

#[cfg(test)]
mod tests {
    use super::*;
    use toml::toml;

    #[test]
    fn test_empty_string_shadowing() {
        let mut base: Value = toml! {
            [system]
            name = "mios"
            description = "Main OS"
        }
        .into();

        let overlay: Value = toml! {
            [system]
            name = ""
            description = "Updated OS"
        }
        .into();

        deep_merge(&mut base, overlay);

        assert_eq!(base["system"]["name"].as_str().unwrap(), "mios");
        assert_eq!(
            base["system"]["description"].as_str().unwrap(),
            "Updated OS"
        );
    }

    #[test]
    fn test_list_replace_not_append() {
        let mut base: Value = toml! {
            ports = [80, 443]
        }
        .into();

        let overlay: Value = toml! {
            ports = [8080]
        }
        .into();

        deep_merge(&mut base, overlay);

        let ports: Vec<i64> = base["ports"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_integer().unwrap())
            .collect();
        assert_eq!(ports, vec![8080]);
    }

    #[test]
    fn test_nested_table_merge() {
        let mut base: Value = toml! {
            [ai.vllm]
            model = "llama"
            tp = 2
        }
        .into();

        let overlay: Value = toml! {
            [ai.vllm]
            model = "qwen"
            gpu_memory_utilization = 0.9
        }
        .into();

        deep_merge(&mut base, overlay);

        assert_eq!(base["ai"]["vllm"]["model"].as_str().unwrap(), "qwen");
        assert_eq!(base["ai"]["vllm"]["tp"].as_integer().unwrap(), 2);
        assert_eq!(
            base["ai"]["vllm"]["gpu_memory_utilization"]
                .as_float()
                .unwrap(),
            0.9
        );
    }

    #[test]
    fn test_absent_layer_noop() {
        let mut base: Value = toml! {
            key = "value"
        }
        .into();

        let overlay = Value::Table(toml::map::Map::new());

        deep_merge(&mut base, overlay);

        assert_eq!(base["key"].as_str().unwrap(), "value");
    }

    #[test]
    fn empty_string_never_overrides_a_non_string_below_it() {
        let mut base: Value = toml! {
            [s]
            port = 8080
            off = false
            zero = 0
            list = ["a"]
            empty_list = []
            [s.table]
            leaf = "x"
        }
        .into();
        let before = base.clone();
        let overlay: Value = toml! {
            [s]
            port = ""
            off = ""
            zero = ""
            list = ""
            empty_list = ""
            table = ""
        }
        .into();
        deep_merge(&mut base, overlay);
        assert_eq!(base, before);
        // A real value still replaces any type.
        deep_merge(&mut base, toml! { [s] port = "named" }.into());
        assert_eq!(base["s"]["port"].as_str(), Some("named"));
    }

    #[test]
    fn test_empty_string_initialization() {
        let mut base: Value = toml! {
            key = ""
        }
        .into();

        let overlay: Value = toml! {
            other = ""
        }
        .into();

        deep_merge(&mut base, overlay);

        assert_eq!(base["key"].as_str().unwrap(), "");
        assert_eq!(base["other"].as_str().unwrap(), "");
    }
}

#[cfg(test)]
mod diff_tests {
    use super::diff_against;
    use toml::Value;

    fn t(s: &str) -> Value {
        s.parse::<Value>().unwrap_or_else(|e| panic!("{e}"))
    }

    #[test]
    fn only_changed_and_new_keys_survive() {
        let base = t("[ai]\nendpoint = \"a\"\nmodel = \"m\"\n[ports]\nx = 1\n");
        let new = t("[ai]\nendpoint = \"b\"\nmodel = \"m\"\n[ports]\nx = 1\n[extra]\nk = true\n");
        let d = diff_against(&new, &base).unwrap_or_else(|| panic!("diff expected"));
        assert_eq!(d, t("[ai]\nendpoint = \"b\"\n[extra]\nk = true\n"));
    }

    #[test]
    fn identical_documents_have_no_diff() {
        let base = t("[ai]\nendpoint = \"a\"\nlist = [1, 2]\n");
        assert_eq!(diff_against(&base, &base), None);
    }
}
