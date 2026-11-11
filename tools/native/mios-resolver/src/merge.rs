// AI-hint: Rust module for merging TOML trees according to MiOS overlay semantics.
use toml::Value;

/// Normalize the former game schema in ONE raw configuration layer before
/// merging tiers. Within that layer canonical values win; a higher legacy
/// layer still outranks a lower canonical layer. This is input compatibility,
/// not a replacement of arbitrary strings, paths, environment or saved state.
pub fn normalize_mons_layer(layer: &mut Value) {
    fn rename(table: &mut toml::Table) {
        if let Some(mut previous) = table.remove("tamagotchi") {
            if let Some(current) = table.remove("mons") {
                deep_merge(&mut previous, current);
            }
            table.insert("mons".into(), previous);
        }
    }
    fn at<'a>(value: &'a mut Value, path: &[&str]) -> Option<&'a mut Value> {
        let mut current = value;
        for part in path {
            current = current.as_table_mut()?.get_mut(*part)?;
        }
        Some(current)
    }
    fn token(value: &mut Value, old: &str, new: &str) {
        if value.as_str() == Some(old) {
            *value = Value::String(new.into());
        }
    }
    fn tokens(value: &mut Value, old: &str, new: &str) {
        if let Some(items) = value.as_array_mut() {
            for item in items {
                token(item, old, new);
            }
        }
    }
    let Some(root) = layer.as_table_mut() else {
        return;
    };
    rename(root);
    if let Some(categories) = at(layer, &["rust", "categories"]).and_then(Value::as_table_mut) {
        rename(categories);
        for group in ["owners", "roles"] {
            if let Some(groups) = categories.get_mut(group).and_then(Value::as_table_mut) {
                for members in groups.iter_mut().map(|(_, value)| value) {
                    tokens(members, "tamagotchi", "mons");
                }
            }
        }
        if let Some(binaries) = categories.get_mut("binaries") {
            tokens(binaries, "mios-tamagotchi", "mios-mons");
        }
        // The category's executable/crate declarations are typed names. Its
        // scope/replaces paths, prose and arbitrary values remain untouched.
        for category in categories
            .iter_mut()
            .filter_map(|(_, value)| value.as_table_mut())
        {
            if let Some(binary) = category.get_mut("binary") {
                token(binary, "mios-tamagotchi", "mios-mons");
            }
            if let Some(crates) = category.get_mut("crates") {
                tokens(crates, "mios-tamagotchi", "mios-mons");
            }
        }
    }
    if let Some(excluded) = at(layer, &["generation", "walk", "excluded_sections"]) {
        tokens(excluded, "tamagotchi", "mons");
    }
    // A retained higher-tier canonical app list must not select the legacy
    // symlink. User executables/custom paths keep their existing strict proof.
    if let Some(executables) = at(
        layer,
        &[
            "keybindings",
            "global",
            "controller",
            "nav",
            "apps",
            "mon",
            "exe",
        ],
    ) {
        tokens(
            executables,
            "/usr/bin/mios-tamagotchi",
            "/usr/bin/mios-mons",
        );
    }
    if let Some(native) = at(layer, &["build", "native"]).and_then(Value::as_table_mut) {
        for field in ["windows_only", "windows_shared"] {
            if let Some(items) = native.get_mut(field) {
                tokens(items, "mios-tamagotchi", "mios-mons");
            }
        }
        if let Some(categories) = native.get_mut("categories").and_then(Value::as_table_mut) {
            for category in categories
                .iter_mut()
                .filter_map(|(_, value)| value.as_table_mut())
            {
                if let Some(binaries) = category.get_mut("binaries") {
                    tokens(binaries, "mios-tamagotchi", "mios-mons");
                }
            }
        }
        if let Some(aliases) = native.get_mut("aliases").and_then(Value::as_table_mut) {
            for alias in aliases
                .iter_mut()
                .filter_map(|(_, value)| value.as_table_mut())
            {
                if let Some(binary) = alias.get_mut("binary") {
                    token(binary, "mios-tamagotchi", "mios-mons");
                }
            }
        }
    }
    if let Some(popups) = at(layer, &["terminal", "popups"]).and_then(Value::as_table_mut) {
        for field in [
            "window_size_source",
            "menu_width_source",
            "menu_height_source",
            "dialog_width_source",
            "dialog_height_source",
            "tooltip_width_source",
            "tooltip_height_source",
            "font_width_source",
            "font_height_source",
            "aspect_source",
        ] {
            if let Some(value) = popups.get_mut(field) {
                if let Some(rest) = value.as_str().and_then(|s| s.strip_prefix("tamagotchi.")) {
                    *value = Value::String(format!("mons.{rest}"));
                }
            }
        }
    }
}

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
    fn game_schema_aliases_are_one_layer_only_and_idempotent() {
        let mut layer: Value = toml::from_str(
            "[tamagotchi]\nzoom_percent=125\npalette=['old']\nkeep=7\n\
             [mons]\nzoom_percent=150\npalette=['new']\nkeep=''\n",
        )
        .unwrap();
        normalize_mons_layer(&mut layer);
        assert!(layer.get("tamagotchi").is_none());
        assert_eq!(layer["mons"]["zoom_percent"].as_integer(), Some(150));
        assert_eq!(layer["mons"]["keep"].as_integer(), Some(7));
        assert_eq!(layer["mons"]["palette"], toml! { value = ["new"] }["value"]);
        let once = layer.clone();
        normalize_mons_layer(&mut layer);
        assert_eq!(layer, once);
        for (source, expected) in [
            ("tamagotchi=false", "mons=false"),
            ("tamagotchi=false\nmons=''", "mons=false"),
            ("tamagotchi={}\nmons=false", "mons=false"),
            ("rust=false\nterminal=[]", "rust=false\nterminal=[]"),
        ] {
            let mut value = source.parse::<Value>().unwrap();
            normalize_mons_layer(&mut value);
            assert_eq!(value, expected.parse::<Value>().unwrap());
        }
    }

    #[test]
    fn game_aliases_rewrite_only_declared_tokens_and_source_pointers() {
        let mut layer: Value = toml::from_str(
            r#"
label = "tamagotchi"
[tamagotchi]
state_directory = "mios/tamagotchi"
[rust.categories]
binaries = ["mios-tamagotchi", "other", "mios-tamagotchi-extra"]
[rust.categories.tamagotchi]
binary = "mios-tamagotchi"
scope = ["tools/native/mios-tamagotchi"]
description = "tamagotchi"
[rust.categories.owners]
lane = ["tamagotchi", 7, "other"]
[rust.categories.roles]
apps = ["tamagotchi"]
[generation.walk]
excluded_sections = ["units", "tamagotchi"]
[build.native]
windows_shared = ["mios-tamagotchi"]
[build.native.categories.apps]
binaries = ["mios-tamagotchi"]
[build.native.aliases.old-launch]
binary = "mios-tamagotchi"
command = "mios-tamagotchi"
[terminal.popups]
menu_width_source = "tamagotchi.popup_min_width"
font_width_source = "tamagotchi.camera.width"
description = "tamagotchi.popup_min_width"
[terminal.custom]
menu_width_source = "tamagotchi.popup_min_width"
[keybindings.global.controller.nav.apps.mon]
exe = ["/usr/bin/mios-tamagotchi", "/custom/mios-tamagotchi", "usr/bin/mios-tamagotchi"]
user_exe = ["/usr/bin/mios-tamagotchi"]
[env]
MIOS_TAMAGOTCHI = "untouched"
"#,
        )
        .unwrap();
        normalize_mons_layer(&mut layer);
        assert_eq!(
            layer["mons"]["state_directory"].as_str(),
            Some("mios/tamagotchi")
        );
        assert_eq!(layer["label"].as_str(), Some("tamagotchi"));
        assert_eq!(
            layer["rust"]["categories"]["mons"]["binary"].as_str(),
            Some("mios-mons")
        );
        assert!(layer["rust"]["categories"].get("tamagotchi").is_none());
        assert_eq!(
            layer["rust"]["categories"]["mons"]["scope"][0].as_str(),
            Some("tools/native/mios-tamagotchi")
        );
        assert_eq!(
            layer["rust"]["categories"]["owners"]["lane"][0].as_str(),
            Some("mons")
        );
        assert_eq!(
            layer["rust"]["categories"]["owners"]["lane"][1].as_integer(),
            Some(7)
        );
        assert_eq!(
            layer["generation"]["walk"]["excluded_sections"][1].as_str(),
            Some("mons")
        );
        assert_eq!(
            layer["build"]["native"]["categories"]["apps"]["binaries"][0].as_str(),
            Some("mios-mons")
        );
        assert_eq!(
            layer["build"]["native"]["aliases"]["old-launch"]["command"].as_str(),
            Some("mios-tamagotchi")
        );
        assert_eq!(
            layer["terminal"]["popups"]["menu_width_source"].as_str(),
            Some("mons.popup_min_width")
        );
        assert_eq!(
            layer["terminal"]["popups"]["description"].as_str(),
            Some("tamagotchi.popup_min_width")
        );
        assert_eq!(
            layer["terminal"]["custom"]["menu_width_source"].as_str(),
            Some("tamagotchi.popup_min_width")
        );
        let app = &layer["keybindings"]["global"]["controller"]["nav"]["apps"]["mon"];
        assert_eq!(app["exe"][0].as_str(), Some("/usr/bin/mios-mons"));
        assert_eq!(app["exe"][1].as_str(), Some("/custom/mios-tamagotchi"));
        assert_eq!(app["exe"][2].as_str(), Some("usr/bin/mios-tamagotchi"));
        assert_eq!(
            app["user_exe"][0].as_str(),
            Some("/usr/bin/mios-tamagotchi")
        );
        assert_eq!(layer["env"]["MIOS_TAMAGOTCHI"].as_str(), Some("untouched"));
    }

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
