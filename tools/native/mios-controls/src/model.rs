// AI-hint: mios-controls model: loads the layered SSOT (vendor+host below the user tier, the user tier as an editable document), holds the binding draft and projects it back into the user tier touching only [keybindings.global] bindings and controller.system_layer.
// AI-related: tools/native/mios-resolver/src/layers.rs, tools/native/mios-resolver/src/merge.rs, usr/share/mios/mios.toml [keybindings.global]
// AI-functions: Layers::load, Layers::effective, Layers::user_doc_for, Draft::from_merged, keybindings_fragment
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use toml::Value;

/// The closed set of contexts a binding can be active in (the gate's schema).
pub const CONTEXTS: &[&str] = &["desktop-2d", "desktop-3d", "terminal", "vm-passthrough", "game", "app-capture"];

/// Controller buttons the diagrams show, in diagram order, with their short labels.
pub const PAD_BUTTONS: &[(&str, &str)] = &[
    ("BTN_TL2", "LT"),
    ("BTN_TL", "LB"),
    ("BTN_TR", "RB"),
    ("BTN_TR2", "RT"),
    ("BTN_DPAD_UP", "Up"),
    ("BTN_DPAD_LEFT", "Left"),
    ("BTN_DPAD_RIGHT", "Right"),
    ("BTN_DPAD_DOWN", "Down"),
    ("BTN_SELECT", "Select"),
    ("BTN_MODE", "Guide"),
    ("BTN_START", "Start"),
    ("BTN_NORTH", "N"),
    ("BTN_WEST", "W"),
    ("BTN_EAST", "E"),
    ("BTN_SOUTH", "S"),
    ("BTN_THUMBL", "L3"),
    ("BTN_THUMBR", "R3"),
];

pub fn at<'a>(v: &'a Value, path: &str) -> Option<&'a Value> {
    path.split('.').try_fold(v, |v, k| v.get(k))
}

pub fn str_list(v: Option<&Value>) -> Vec<String> {
    v.and_then(Value::as_array)
        .map(|a| a.iter().filter_map(|x| x.as_str().map(str::to_owned)).collect())
        .unwrap_or_default()
}

/// The editable part of [keybindings.global]: the bindings array and the controller system layer.
#[derive(Clone, Debug, PartialEq)]
pub struct Draft {
    pub bindings: Vec<toml::Table>,
    pub system_layer: BTreeMap<String, String>,
}

impl Draft {
    pub fn from_merged(merged: &Value) -> Self {
        let bindings = at(merged, "keybindings.global.bindings")
            .and_then(Value::as_array)
            .map(|a| a.iter().filter_map(|b| b.as_table().cloned()).collect())
            .unwrap_or_default();
        let system_layer = at(merged, "keybindings.global.controller.system_layer")
            .and_then(Value::as_table)
            .map(|t| t.iter().filter_map(|(k, v)| v.as_str().map(|s| (k.clone(), s.to_owned()))).collect())
            .unwrap_or_default();
        Self { bindings, system_layer }
    }

    pub fn index(&self, id: &str) -> Option<usize> {
        self.bindings.iter().position(|b| b.get("id").and_then(Value::as_str) == Some(id))
    }
}

pub fn field<'a>(b: &'a toml::Table, key: &str) -> &'a str {
    b.get(key).and_then(Value::as_str).unwrap_or("")
}

pub struct Layers {
    pub root: PathBuf,
    pub user_path: PathBuf,
    /// vendor + host tiers (everything a user-tier write sits on)
    pub base: Value,
    /// the user tier exactly as read (empty when the file does not exist)
    pub user_text: String,
    pub user_value: Value,
}

impl Layers {
    pub fn load(root: &Path) -> Result<Self, String> {
        let base = mios_resolver::resolve_below_user(Some(root)).map_err(|e| format!("SSOT below the user tier: {e}"))?;
        if at(&base, "keybindings.global").and_then(Value::as_table).is_none() {
            return Err("the SSOT declares no [keybindings.global] table".into());
        }
        let user_path = mios_resolver::user_toml_path(Some(root));
        let user_text = match std::fs::read_to_string(&user_path) {
            Ok(t) => t,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => String::new(),
            Err(e) => return Err(format!("{}: {e}", user_path.display())),
        };
        let user_value = parse_value(&user_text).map_err(|e| format!("{}: {e}", user_path.display()))?;
        Ok(Self { root: root.to_owned(), user_path, base, user_text, user_value })
    }

    pub fn merged_with(&self, user: Value) -> Value {
        let mut merged = self.base.clone();
        mios_resolver::merge::deep_merge(&mut merged, user);
        merged
    }

    pub fn effective(&self) -> Value {
        self.merged_with(self.user_value.clone())
    }

    pub fn defaults(&self) -> Draft {
        Draft::from_merged(&self.base)
    }

    /// The user tier with `draft` projected into it. Arrays replace across tiers, so the bindings array is
    /// written whole when it differs from the tiers below and removed when it matches them; system-layer
    /// buttons are written only where they differ. Nothing outside [keybindings.global] is touched.
    pub fn user_doc_for(&self, draft: &Draft) -> Result<toml_edit::Document, String> {
        let mut doc: toml_edit::Document = self.user_text.parse().map_err(|e| format!("{}: {e}", self.user_path.display()))?;
        let defaults = self.defaults();
        let root = doc.as_table_mut();
        let global = ensure_table(ensure_table(root, "keybindings")?, "global")?;
        if draft.bindings == defaults.bindings {
            global.remove("bindings");
        } else {
            let mut aot = toml_edit::ArrayOfTables::new();
            for b in &draft.bindings {
                aot.push(to_edit_table(b)?);
            }
            global.insert("bindings", toml_edit::Item::ArrayOfTables(aot));
        }
        {
            let layer = ensure_table(ensure_table(global, "controller")?, "system_layer")?;
            let keys: Vec<String> = layer.iter().map(|(k, _)| k.to_owned()).collect();
            for k in keys {
                if !draft.system_layer.contains_key(&k) || defaults.system_layer.get(&k) == draft.system_layer.get(&k) {
                    layer.remove(&k);
                }
            }
            for (k, v) in &draft.system_layer {
                if defaults.system_layer.get(k) != Some(v) {
                    layer.insert(k, toml_edit::value(v.as_str()));
                }
            }
        }
        prune_empty(root, &["keybindings", "global", "controller", "system_layer"]);
        Ok(doc)
    }

    /// A vendor/host system-layer button cannot be removed from a higher tier (tables merge per key and an empty
    /// string never shadows a value below it); report that instead of writing an inert override.
    pub fn removable_button(&self, button: &str) -> bool {
        !self.defaults().system_layer.contains_key(button)
    }
}

pub fn parse_value(text: &str) -> Result<Value, String> {
    if text.trim().is_empty() {
        return Ok(Value::Table(toml::Table::new()));
    }
    text.parse::<Value>().map_err(|e| e.to_string())
}

fn ensure_table<'a>(t: &'a mut toml_edit::Table, key: &str) -> Result<&'a mut toml_edit::Table, String> {
    if !t.contains_key(key) {
        let mut child = toml_edit::Table::new();
        child.set_implicit(true);
        t.insert(key, toml_edit::Item::Table(child));
    }
    t.get_mut(key)
        .and_then(toml_edit::Item::as_table_mut)
        .ok_or_else(|| format!("user tier: {key} is not a table"))
}

/// Remove the tables on `path` (deepest first) that are left empty, so a restored default leaves no stub headers.
fn prune_empty(root: &mut toml_edit::Table, path: &[&str]) {
    if path.is_empty() {
        return;
    }
    if let Some(child) = root.get_mut(path[0]).and_then(toml_edit::Item::as_table_mut) {
        prune_empty(child, &path[1..]);
        if child.is_empty() {
            root.remove(path[0]);
        }
    }
}

fn to_edit_table(t: &toml::Table) -> Result<toml_edit::Table, String> {
    let text = toml::to_string(t).map_err(|e| e.to_string())?;
    let doc: toml_edit::Document = text.parse().map_err(|e: toml_edit::TomlError| e.to_string())?;
    Ok(doc.as_table().clone())
}

/// A portable fragment carrying only the binding map: what export writes and import reads. It never contains
/// anything else from the user tier (which also holds credentials).
pub fn keybindings_fragment(draft: &Draft) -> Result<String, String> {
    let mut global = toml::Table::new();
    global.insert("bindings".into(), Value::Array(draft.bindings.iter().cloned().map(Value::Table).collect()));
    let layer: toml::Table = draft.system_layer.iter().map(|(k, v)| (k.clone(), Value::String(v.clone()))).collect();
    let mut controller = toml::Table::new();
    controller.insert("system_layer".into(), Value::Table(layer));
    global.insert("controller".into(), Value::Table(controller));
    let mut kb = toml::Table::new();
    kb.insert("global".into(), Value::Table(global));
    let mut doc = toml::Table::new();
    doc.insert("keybindings".into(), Value::Table(kb));
    toml::to_string(&doc).map_err(|e| e.to_string())
}

/// Read an exported fragment over `current`: keys the fragment carries replace the draft's; anything outside the
/// binding map is refused rather than silently dropped.
pub fn import_fragment(text: &str, current: &Draft) -> Result<Draft, String> {
    let v = parse_value(text)?;
    let top: Vec<&String> = v.as_table().map(|t| t.keys().collect()).unwrap_or_default();
    if top.iter().any(|k| k.as_str() != "keybindings") {
        return Err(format!("import carries tables outside [keybindings]: {top:?}"));
    }
    if let Some(g) = at(&v, "keybindings.global").and_then(Value::as_table) {
        if let Some(k) = g.keys().find(|k| !matches!(k.as_str(), "bindings" | "controller")) {
            return Err(format!("import may carry only bindings and controller.system_layer, not {k}"));
        }
        if let Some(c) = g.get("controller").and_then(Value::as_table) {
            if let Some(k) = c.keys().find(|k| k.as_str() != "system_layer") {
                return Err(format!("import may carry only controller.system_layer, not controller.{k}"));
            }
        }
    }
    let mut out = current.clone();
    if let Some(a) = at(&v, "keybindings.global.bindings") {
        let arr = a.as_array().ok_or("keybindings.global.bindings is not an array")?;
        out.bindings = arr
            .iter()
            .map(|b| b.as_table().cloned().ok_or_else(|| "a binding is not a table".to_string()))
            .collect::<Result<_, _>>()?;
    }
    if let Some(t) = at(&v, "keybindings.global.controller.system_layer") {
        let t = t.as_table().ok_or("controller.system_layer is not a table")?;
        for (k, val) in t {
            let s = val.as_str().ok_or_else(|| format!("system_layer.{k} is not a string"))?;
            out.system_layer.insert(k.clone(), s.to_owned());
        }
    }
    Ok(out)
}

/// The user tier with every table except [keybindings] -- the invariant apply proves before writing.
pub fn without_keybindings(v: &Value) -> Value {
    let mut v = v.clone();
    if let Some(t) = v.as_table_mut() {
        t.remove("keybindings");
    }
    v
}

#[cfg(test)]
mod tests {
    use super::*;

    const VENDOR: &str = r#"
[keybindings.global]
enabled = true
[[keybindings.global.bindings]]
id = "terminal"
tier = 1
chord = "SUPER+Return"
action = "exec:mios-terminal"
[keybindings.global.controller.system_layer]
BTN_SOUTH = "hypr:togglefloating"
"#;

    fn layers(user: &str) -> (tempfile::TempDir, Layers) {
        let d = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(d.path().join("usr/share/mios")).unwrap();
        std::fs::write(d.path().join("usr/share/mios/mios.toml"), VENDOR).unwrap();
        let base: Value = VENDOR.parse().unwrap();
        let user_value = parse_value(user).unwrap();
        let l = Layers { root: d.path().into(), user_path: d.path().join("user.toml"), base, user_text: user.into(), user_value };
        (d, l)
    }

    #[test]
    fn unchanged_draft_leaves_the_user_tier_byte_identical() {
        let user = "# mine\n[identity]\nsecret = \"x\"\n";
        let (_d, l) = layers(user);
        let doc = l.user_doc_for(&Draft::from_merged(&l.effective())).unwrap();
        assert_eq!(doc.to_string(), user);
    }

    #[test]
    fn a_rebind_writes_the_whole_array_and_restore_removes_it() {
        let user = "[identity]\nsecret = \"x\"\n";
        let (_d, mut l) = layers(user);
        let mut draft = Draft::from_merged(&l.effective());
        draft.bindings[0].insert("chord".into(), Value::String("SUPER+t".into()));
        draft.system_layer.insert("BTN_WEST".into(), "exec:foot".into());
        let text = l.user_doc_for(&draft).unwrap().to_string();
        assert!(text.starts_with(user), "other tables keep their bytes: {text}");
        let v = parse_value(&text).unwrap();
        assert_eq!(at(&v, "keybindings.global.bindings").unwrap().as_array().unwrap().len(), 1);
        assert_eq!(at(&v, "keybindings.global.controller.system_layer.BTN_WEST").unwrap().as_str(), Some("exec:foot"));
        assert!(at(&v, "keybindings.global.controller.system_layer.BTN_SOUTH").is_none(), "unchanged button not frozen");
        assert_eq!(without_keybindings(&v), without_keybindings(&l.user_value));
        // restore defaults over the written tier removes every override and every stub header
        l.user_text = text;
        l.user_value = v;
        let restored = l.user_doc_for(&l.defaults()).unwrap().to_string();
        assert_eq!(restored.trim_end(), user.trim_end());
    }

    #[test]
    fn import_refuses_anything_outside_the_binding_map() {
        let (_d, l) = layers("");
        let cur = l.defaults();
        assert!(import_fragment("[identity]\nuser_password = \"p\"\n", &cur).is_err());
        assert!(import_fragment("[keybindings.global]\nbypass_mod = \"ALT\"\n", &cur).is_err());
        assert!(import_fragment("[keybindings.global.controller]\nexclusive_grab = false\n", &cur).is_err());
        let round = import_fragment(&keybindings_fragment(&cur).unwrap(), &cur).unwrap();
        assert_eq!(round, cur);
        let one = import_fragment("[keybindings.global.controller.system_layer]\nBTN_WEST = \"exec:foot\"\n", &cur).unwrap();
        assert_eq!(one.bindings, cur.bindings);
        assert_eq!(one.system_layer.get("BTN_WEST").map(String::as_str), Some("exec:foot"));
    }

    #[test]
    fn vendor_buttons_are_not_removable_from_the_user_tier() {
        let (_d, l) = layers("");
        assert!(!l.removable_button("BTN_SOUTH"));
        assert!(l.removable_button("BTN_WEST"));
    }
}
