// AI-hint: mios-controls model: loads the layered SSOT (vendor+host below the user tier, the user tier as an editable document), holds the binding draft (global bindings, Guide layer, Hypr3D room table, tmux-os prefix keys, compositor binding tables) and projects it back into the user tier touching only the [desktop.controls].editable paths.
// AI-related: tools/native/mios-resolver/src/layers.rs, tools/native/mios-resolver/src/merge.rs, usr/share/mios/mios.toml
// AI-functions: Layers::load, Layers::effective, Layers::user_doc_for, Draft::from_merged, keybindings_fragment, import_fragment, without_paths
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use toml::Value;
/// A binding array path with its draft, default and current rows.
type BindingArrayLayers<'a> = (
    &'a str,
    &'a [toml::Table],
    &'a [toml::Table],
    &'a [toml::Table],
);

/// The closed set of contexts a binding can be active in (the gate's schema). `console` (the Linux VT) is never in
/// a row's default set: a row without `contexts` is active in every context except the console.
pub const CONTEXTS: &[&str] = &[
    "desktop-2d",
    "desktop-3d",
    "terminal",
    "vm-passthrough",
    "game",
    "app-capture",
    "console",
];
pub const CONSOLE: &str = "console";

/// The default context set of a row without `contexts`.
pub fn default_contexts() -> impl Iterator<Item = &'static str> {
    CONTEXTS.iter().copied().filter(|c| *c != CONSOLE)
}

/// The L3+R3-style toggle chord, from its one source ([desktop.hyprland.hypr3d.controller].toggle_chord).
pub fn toggle_chord(merged: &Value) -> Vec<String> {
    str_list(at(
        merged,
        "desktop.hyprland.hypr3d.controller.toggle_chord",
    ))
}

pub const GLOBAL_BINDINGS: &str = "keybindings.global.bindings";
pub const SYSTEM_LAYER: &str = "keybindings.global.controller.system_layer";
pub const ROOM: &str = "keybindings.global.controller.room";
pub const TMUX_BINDINGS: &str = "keybindings.tmux.bindings";

/// The user-tier paths an editor without an SSOT list may write (the original contract).
pub const DEFAULT_EDITABLE: &[&str] = &[GLOBAL_BINDINGS, SYSTEM_LAYER];

/// An explicit, non-default root: its own tiers win over the process's tier pointers.
pub fn rooted(root: &Path) -> bool {
    root != Path::new("/")
}

pub fn at<'a>(v: &'a Value, path: &str) -> Option<&'a Value> {
    path.split('.').try_fold(v, |v, k| v.get(k))
}

pub fn str_list(v: Option<&Value>) -> Vec<String> {
    v.and_then(Value::as_array)
        .map(|a| {
            a.iter()
                .filter_map(|x| x.as_str().map(str::to_owned))
                .collect()
        })
        .unwrap_or_default()
}

fn tables_at(merged: &Value, path: &str) -> Vec<toml::Table> {
    at(merged, path)
        .and_then(Value::as_array)
        .map(|a| a.iter().filter_map(|b| b.as_table().cloned()).collect())
        .unwrap_or_default()
}

/// Is `path` one of the compositor binding tables ([desktop.<x>].bindings arrays of { key = ... }) the draft carries?
pub fn is_desktop_table(path: &str) -> bool {
    path.starts_with("desktop.") && path.ends_with(".bindings")
}

/// The editable binding map: the global bindings array, the Guide layer, the Hypr3D room table, tmux-os prefix keys
/// and the compositor binding tables.
#[derive(Clone, Debug, PartialEq)]
pub struct Draft {
    pub bindings: Vec<toml::Table>,
    pub system_layer: BTreeMap<String, String>,
    /// [keybindings.global.controller.room]: "ns:action", "none" or ["tap", "hold"]
    pub room: BTreeMap<String, Value>,
    pub tmux: Vec<toml::Table>,
    /// desktop.<x>.bindings path -> entries (each carries `key`)
    pub tables: BTreeMap<String, Vec<toml::Table>>,
}

impl Draft {
    pub fn from_merged(merged: &Value) -> Self {
        let system_layer = at(merged, SYSTEM_LAYER)
            .and_then(Value::as_table)
            .map(|t| {
                t.iter()
                    .filter_map(|(k, v)| v.as_str().map(|s| (k.clone(), s.to_owned())))
                    .collect()
            })
            .unwrap_or_default();
        let room = at(merged, ROOM)
            .and_then(Value::as_table)
            .map(|t| t.iter().map(|(k, v)| (k.clone(), v.clone())).collect())
            .unwrap_or_default();
        let mut tables = BTreeMap::new();
        for path in desktop_table_paths(merged) {
            tables.insert(path.clone(), tables_at(merged, &path));
        }
        Self {
            bindings: tables_at(merged, GLOBAL_BINDINGS),
            system_layer,
            room,
            tmux: tables_at(merged, TMUX_BINDINGS),
            tables,
        }
    }

    pub fn index(&self, id: &str) -> Option<usize> {
        self.bindings
            .iter()
            .position(|b| b.get("id").and_then(Value::as_str) == Some(id))
    }
}

/// Every [desktop.*] `bindings` array whose entries carry `key` strings (the gate's legacy compositor tables).
pub fn desktop_table_paths(merged: &Value) -> Vec<String> {
    fn walk(v: &Value, path: String, out: &mut Vec<String>) {
        if let Some(t) = v.as_table() {
            for (k, child) in t {
                let p = format!("{path}.{k}");
                if k == "bindings"
                    && child.as_array().is_some_and(|a| {
                        a.iter()
                            .any(|e| e.get("key").and_then(Value::as_str).is_some())
                    })
                {
                    out.push(p.clone());
                }
                walk(child, p, out);
            }
        }
    }
    let mut out = Vec::new();
    if let Some(d) = merged.get("desktop") {
        walk(d, "desktop".into(), &mut out);
    }
    out
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
    /// `--root R` other than `/` reads R's vendor and host tiers, whatever MIOS_TOML / MIOS_VENDOR_TOML point at
    /// (a session that exports them must not redirect an explicit root). `/` keeps the layered environment.
    pub fn load(root: &Path) -> Result<Self, String> {
        let base = if rooted(root) {
            mios_resolver::resolve_projection(root)
        } else {
            mios_resolver::resolve_below_user(Some(root))
        }
        .map_err(|e| format!("SSOT below the user tier: {e}"))?;
        if at(&base, "keybindings.global")
            .and_then(Value::as_table)
            .is_none()
        {
            return Err("the SSOT declares no [keybindings.global] table".into());
        }
        let user_path = mios_resolver::user_toml_path(Some(root));
        let user_text = match std::fs::read_to_string(&user_path) {
            Ok(t) => t,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => String::new(),
            Err(e) => return Err(format!("{}: {e}", user_path.display())),
        };
        let user_value =
            parse_value(&user_text).map_err(|e| format!("{}: {e}", user_path.display()))?;
        Ok(Self {
            root: root.to_owned(),
            user_path,
            base,
            user_text,
            user_value,
        })
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

    /// The user-tier paths this editor may write: [desktop.controls].editable from the tiers BELOW the user tier (a
    /// user tier cannot widen what the editor writes into it), else the original two.
    pub fn editable(&self) -> Vec<String> {
        let list = str_list(at(&self.base, "desktop.controls.editable"));
        if list.is_empty() {
            DEFAULT_EDITABLE.iter().map(|s| s.to_string()).collect()
        } else {
            list
        }
    }

    /// The user tier with `draft` projected into it. Arrays replace across tiers, so an array is written whole when it
    /// differs from the tiers below and removed when it matches them; map entries are written only where they differ.
    /// Nothing outside the editable paths is touched, and a draft change to a path that is not editable is an error.
    pub fn user_doc_for(&self, draft: &Draft) -> Result<toml_edit::Document, String> {
        let mut doc: toml_edit::Document = self
            .user_text
            .parse()
            .map_err(|e| format!("{}: {e}", self.user_path.display()))?;
        let defaults = self.defaults();
        let current = Draft::from_merged(&self.effective());
        let editable = self.editable();
        let allowed = |p: &str| editable.iter().any(|e| e == p);
        let refuse = |p: &str| -> Result<(), String> {
            Err(format!(
                "{p} is not editable here ([desktop.controls].editable)"
            ))
        };
        let root = doc.as_table_mut();
        let empty = Vec::new();
        let mut arrays: Vec<BindingArrayLayers<'_>> = vec![
            (
                GLOBAL_BINDINGS,
                &draft.bindings,
                &defaults.bindings,
                &current.bindings,
            ),
            (TMUX_BINDINGS, &draft.tmux, &defaults.tmux, &current.tmux),
        ];
        for (p, entries) in &draft.tables {
            arrays.push((
                p.as_str(),
                entries,
                defaults.tables.get(p).unwrap_or(&empty),
                current.tables.get(p).unwrap_or(&empty),
            ));
        }
        for (path, want, below, now) in arrays {
            if !allowed(path) {
                if want != now {
                    refuse(path)?;
                }
                continue;
            }
            let (parent, leaf) = path.rsplit_once('.').ok_or("bad editable path")?;
            let table = ensure_path(root, parent)?;
            if want == below {
                table.remove(leaf);
            } else {
                let mut aot = toml_edit::ArrayOfTables::new();
                for b in want {
                    aot.push(to_edit_table(b)?);
                }
                table.insert(leaf, toml_edit::Item::ArrayOfTables(aot));
            }
        }
        let layer_value = |m: &BTreeMap<String, String>| -> BTreeMap<String, Value> {
            m.iter()
                .map(|(k, v)| (k.clone(), Value::String(v.clone())))
                .collect()
        };
        let maps = [
            (
                SYSTEM_LAYER,
                layer_value(&draft.system_layer),
                layer_value(&defaults.system_layer),
                layer_value(&current.system_layer),
            ),
            (
                ROOM,
                draft.room.clone(),
                defaults.room.clone(),
                current.room.clone(),
            ),
        ];
        for (path, want, below, now) in maps {
            if !allowed(path) {
                if want != now {
                    refuse(path)?;
                }
                continue;
            }
            let layer = ensure_path(root, path)?;
            let keys: Vec<String> = layer.iter().map(|(k, _)| k.to_owned()).collect();
            for k in keys {
                if !want.contains_key(&k) || below.get(&k) == want.get(&k) {
                    layer.remove(&k);
                }
            }
            for (k, v) in &want {
                if below.get(k) != Some(v) {
                    layer.insert(k, toml_edit::Item::Value(to_edit_value(v)?));
                }
            }
        }
        for path in &editable {
            let segs: Vec<&str> = path.split('.').collect();
            prune_empty(root, &segs, &self.user_value, "");
        }
        Ok(doc)
    }

    /// A vendor/host map entry cannot be removed from a higher tier (tables merge per key and an empty string never
    /// shadows a value below it); report that instead of writing an inert override.
    pub fn removable_button(&self, button: &str) -> bool {
        !self.defaults().system_layer.contains_key(button)
    }

    pub fn removable_room(&self, button: &str) -> bool {
        !self.defaults().room.contains_key(button)
    }
}

pub fn parse_value(text: &str) -> Result<Value, String> {
    if text.trim().is_empty() {
        return Ok(Value::Table(toml::Table::new()));
    }
    text.parse::<Value>().map_err(|e| e.to_string())
}

fn ensure_table<'a>(
    t: &'a mut toml_edit::Table,
    key: &str,
) -> Result<&'a mut toml_edit::Table, String> {
    if !t.contains_key(key) {
        let mut child = toml_edit::Table::new();
        child.set_implicit(true);
        t.insert(key, toml_edit::Item::Table(child));
    }
    t.get_mut(key)
        .and_then(toml_edit::Item::as_table_mut)
        .ok_or_else(|| format!("user tier: {key} is not a table"))
}

fn ensure_path<'a>(
    t: &'a mut toml_edit::Table,
    path: &str,
) -> Result<&'a mut toml_edit::Table, String> {
    path.split('.').try_fold(t, ensure_table)
}

/// Remove the tables on `path` (deepest first) that are left empty, so a restored default leaves no stub headers --
/// except a table the user tier itself held empty (that header is the user's, and apply proves it unchanged).
fn prune_empty(root: &mut toml_edit::Table, path: &[&str], original: &Value, prefix: &str) {
    if path.is_empty() {
        return;
    }
    let here = if prefix.is_empty() {
        path[0].to_owned()
    } else {
        format!("{prefix}.{}", path[0])
    };
    if let Some(child) = root
        .get_mut(path[0])
        .and_then(toml_edit::Item::as_table_mut)
    {
        prune_empty(child, &path[1..], original, &here);
        let user_kept_it_empty = at(original, &here)
            .and_then(Value::as_table)
            .is_some_and(|t| t.is_empty());
        if child.is_empty() && !user_kept_it_empty {
            root.remove(path[0]);
        }
    }
}

fn to_edit_table(t: &toml::Table) -> Result<toml_edit::Table, String> {
    let text = toml::to_string(t).map_err(|e| e.to_string())?;
    let doc: toml_edit::Document = text
        .parse()
        .map_err(|e: toml_edit::TomlError| e.to_string())?;
    Ok(doc.as_table().clone())
}

fn to_edit_value(v: &Value) -> Result<toml_edit::Value, String> {
    let mut t = toml::Table::new();
    t.insert("v".into(), v.clone());
    let text = toml::to_string(&t).map_err(|e| e.to_string())?;
    let doc: toml_edit::Document = text
        .parse()
        .map_err(|e: toml_edit::TomlError| e.to_string())?;
    doc.get("v")
        .and_then(|i| i.as_value())
        .cloned()
        .ok_or_else(|| "value did not round-trip".to_string())
}

/// Set `value` at a dotted path inside a toml table tree, creating tables on the way.
fn set_path(root: &mut toml::Table, path: &str, value: Value) {
    let segs: Vec<&str> = path.split('.').collect();
    let mut t = root;
    for s in &segs[..segs.len() - 1] {
        t = t
            .entry(s.to_string())
            .or_insert_with(|| Value::Table(toml::Table::new()))
            .as_table_mut()
            .expect("fragment table");
    }
    t.insert(segs[segs.len() - 1].to_owned(), value);
}

/// A portable fragment carrying only the binding map (every editable path): what export writes and import reads.
/// It never contains anything else from the user tier (which also holds credentials).
pub fn keybindings_fragment(draft: &Draft, editable: &[String]) -> Result<String, String> {
    let mut doc = toml::Table::new();
    let arr = |v: &Vec<toml::Table>| Value::Array(v.iter().cloned().map(Value::Table).collect());
    for path in editable {
        let value = match path.as_str() {
            GLOBAL_BINDINGS => arr(&draft.bindings),
            TMUX_BINDINGS => arr(&draft.tmux),
            SYSTEM_LAYER => Value::Table(
                draft
                    .system_layer
                    .iter()
                    .map(|(k, v)| (k.clone(), Value::String(v.clone())))
                    .collect(),
            ),
            ROOM => Value::Table(
                draft
                    .room
                    .iter()
                    .map(|(k, v)| (k.clone(), v.clone()))
                    .collect(),
            ),
            p if is_desktop_table(p) => match draft.tables.get(p) {
                Some(v) => arr(v),
                None => continue,
            },
            _ => continue,
        };
        set_path(&mut doc, path, value);
    }
    toml::to_string(&doc).map_err(|e| e.to_string())
}

/// Read an exported fragment over `current`: paths the fragment carries replace the draft's (map entries per key);
/// anything outside the editable binding map is refused rather than silently dropped.
pub fn import_fragment(text: &str, current: &Draft, editable: &[String]) -> Result<Draft, String> {
    let v = parse_value(text)?;
    fn check(v: &Value, prefix: &str, editable: &[String]) -> Result<(), String> {
        let Some(t) = v.as_table() else { return Ok(()) };
        for (k, child) in t {
            let p = if prefix.is_empty() {
                k.clone()
            } else {
                format!("{prefix}.{k}")
            };
            if editable.contains(&p) {
                continue;
            }
            if child.is_table() && editable.iter().any(|e| e.starts_with(&format!("{p}."))) {
                check(child, &p, editable)?;
                continue;
            }
            return Err(format!(
                "import may carry only the editable binding map ({}), not {p}",
                editable.join(", ")
            ));
        }
        Ok(())
    }
    check(&v, "", editable)?;
    let mut out = current.clone();
    let arr = |path: &str| -> Result<Option<Vec<toml::Table>>, String> {
        match at(&v, path) {
            None => Ok(None),
            Some(a) => a
                .as_array()
                .ok_or_else(|| format!("{path} is not an array"))?
                .iter()
                .map(|b| {
                    b.as_table()
                        .cloned()
                        .ok_or_else(|| format!("an entry of {path} is not a table"))
                })
                .collect::<Result<Vec<_>, _>>()
                .map(Some),
        }
    };
    if let Some(b) = arr(GLOBAL_BINDINGS)? {
        out.bindings = b;
    }
    if let Some(b) = arr(TMUX_BINDINGS)? {
        out.tmux = b;
    }
    for p in editable.iter().filter(|p| is_desktop_table(p)) {
        if let Some(b) = arr(p)? {
            out.tables.insert(p.clone(), b);
        }
    }
    if let Some(t) = at(&v, SYSTEM_LAYER) {
        let t = t
            .as_table()
            .ok_or("controller.system_layer is not a table")?;
        for (k, val) in t {
            let s = val
                .as_str()
                .ok_or_else(|| format!("system_layer.{k} is not a string"))?;
            out.system_layer.insert(k.clone(), s.to_owned());
        }
    }
    if let Some(t) = at(&v, ROOM) {
        let t = t.as_table().ok_or("controller.room is not a table")?;
        for (k, val) in t {
            out.room.insert(k.clone(), val.clone());
        }
    }
    Ok(out)
}

/// The user tier with every editable path removed -- the invariant apply proves before writing.
pub fn without_paths(v: &Value, paths: &[String]) -> Value {
    let mut v = v.clone();
    for p in paths {
        let segs: Vec<&str> = p.split('.').collect();
        let mut t = v.as_table_mut();
        for s in &segs[..segs.len() - 1] {
            t = t.and_then(|t| t.get_mut(*s)).and_then(Value::as_table_mut);
        }
        if let Some(t) = t {
            t.remove(segs[segs.len() - 1]);
        }
    }
    prune_value(&mut v);
    v
}

/// Drop empty tables left by without_paths (an implicit parent is not a user setting).
fn prune_value(v: &mut Value) {
    if let Some(t) = v.as_table_mut() {
        for (_, child) in t.iter_mut() {
            prune_value(child);
        }
        t.retain(|_, c| !c.as_table().is_some_and(|t| t.is_empty()));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    pub const VENDOR: &str = r#"
[keybindings.global]
enabled = true
[[keybindings.global.bindings]]
id = "terminal"
tier = 1
chord = "SUPER+Return"
action = "exec:mios-terminal"
[keybindings.global.controller.system_layer]
BTN_SOUTH = "hypr:togglefloating"
[keybindings.global.controller.room]
BTN_EAST = "hypr:hypr3d:cancel"
[keybindings.tmux]
bindings = [ { key = "h", command = "select-pane -L", note = "Pane left" } ]
[desktop.shaderglass]
bindings = [ { key = "SUPER + G", args = "toggle", label = "ShaderGlass" } ]
[desktop.controls]
editable = ["keybindings.global.bindings", "keybindings.global.controller.system_layer", "keybindings.global.controller.room", "keybindings.tmux.bindings", "desktop.shaderglass.bindings"]
"#;

    pub fn layers(user: &str) -> (tempfile::TempDir, Layers) {
        let d = tempfile::tempdir().unwrap();
        let base: Value = VENDOR.parse().unwrap();
        let user_value = parse_value(user).unwrap();
        let l = Layers {
            root: d.path().into(),
            user_path: d.path().join("user.toml"),
            base,
            user_text: user.into(),
            user_value,
        };
        (d, l)
    }

    #[test]
    fn unchanged_draft_leaves_the_user_tier_byte_identical() {
        let user = "# mine\n[identity]\nsecret = \"x\"\n[desktop.hyprland]\n";
        let (_d, l) = layers(user);
        let doc = l.user_doc_for(&Draft::from_merged(&l.effective())).unwrap();
        assert_eq!(
            doc.to_string(),
            user,
            "an empty header the user wrote is kept"
        );
    }

    #[test]
    fn a_rebind_writes_the_whole_array_and_restore_removes_it() {
        let user = "[identity]\nsecret = \"x\"\n";
        let (_d, mut l) = layers(user);
        let mut draft = Draft::from_merged(&l.effective());
        draft.bindings[0].insert("chord".into(), Value::String("SUPER+t".into()));
        draft
            .system_layer
            .insert("BTN_WEST".into(), "exec:foot".into());
        draft
            .room
            .insert("BTN_EAST".into(), Value::String("none".into()));
        draft.tmux[0].insert("key".into(), Value::String("x".into()));
        draft
            .tables
            .get_mut("desktop.shaderglass.bindings")
            .unwrap()[0]
            .insert("key".into(), Value::String("SUPER + F7".into()));
        let text = l.user_doc_for(&draft).unwrap().to_string();
        assert!(
            text.starts_with(user),
            "other tables keep their bytes: {text}"
        );
        let v = parse_value(&text).unwrap();
        assert_eq!(
            at(&v, "keybindings.global.bindings")
                .unwrap()
                .as_array()
                .unwrap()
                .len(),
            1
        );
        assert_eq!(
            at(&v, "keybindings.global.controller.system_layer.BTN_WEST")
                .unwrap()
                .as_str(),
            Some("exec:foot")
        );
        assert!(
            at(&v, "keybindings.global.controller.system_layer.BTN_SOUTH").is_none(),
            "unchanged button not frozen"
        );
        assert_eq!(
            at(&v, "keybindings.global.controller.room.BTN_EAST")
                .unwrap()
                .as_str(),
            Some("none")
        );
        assert_eq!(
            at(&v, "keybindings.tmux.bindings")
                .unwrap()
                .as_array()
                .unwrap()[0]
                .get("key")
                .unwrap()
                .as_str(),
            Some("x")
        );
        assert_eq!(
            at(&v, "desktop.shaderglass.bindings")
                .unwrap()
                .as_array()
                .unwrap()[0]
                .get("key")
                .unwrap()
                .as_str(),
            Some("SUPER + F7")
        );
        let editable = l.editable();
        assert_eq!(
            without_paths(&v, &editable),
            without_paths(&l.user_value, &editable)
        );
        // restore defaults over the written tier removes every override and every stub header
        l.user_text = text;
        l.user_value = v;
        let restored = l.user_doc_for(&l.defaults()).unwrap().to_string();
        assert_eq!(restored.trim_end(), user.trim_end());
    }

    #[test]
    fn a_change_to_a_path_that_is_not_editable_is_refused() {
        let (_d, mut l) = layers("");
        l.base
            .as_table_mut()
            .unwrap()
            .get_mut("desktop")
            .unwrap()
            .as_table_mut()
            .unwrap()
            .remove("controls");
        assert_eq!(
            l.editable(),
            DEFAULT_EDITABLE
                .iter()
                .map(|s| s.to_string())
                .collect::<Vec<_>>()
        );
        let mut draft = Draft::from_merged(&l.effective());
        draft.tmux[0].insert("key".into(), Value::String("x".into()));
        assert!(l
            .user_doc_for(&draft)
            .unwrap_err()
            .contains("keybindings.tmux.bindings is not editable"));
        let mut draft = Draft::from_merged(&l.effective());
        draft
            .room
            .insert("BTN_EAST".into(), Value::String("none".into()));
        assert!(l
            .user_doc_for(&draft)
            .unwrap_err()
            .contains("room is not editable"));
    }

    #[test]
    fn the_user_tier_cannot_widen_what_is_editable() {
        let (_d, l) = layers("[desktop.controls]\neditable = [\"identity\"]\n");
        assert!(!l.editable().contains(&"identity".to_string()));
    }

    #[test]
    fn import_refuses_anything_outside_the_binding_map() {
        let (_d, l) = layers("");
        let cur = l.defaults();
        let ed = l.editable();
        assert!(import_fragment("[identity]\nuser_password = \"p\"\n", &cur, &ed).is_err());
        assert!(
            import_fragment("[keybindings.global]\nbypass_mod = \"ALT\"\n", &cur, &ed).is_err()
        );
        assert!(import_fragment(
            "[keybindings.global.controller]\nexclusive_grab = false\n",
            &cur,
            &ed
        )
        .is_err());
        assert!(import_fragment("[keybindings.tmux]\ntmux_prefix = \"C-a\"\n", &cur, &ed).is_err());
        assert!(
            import_fragment("[desktop.hyprland]\nbindings = []\n", &cur, &ed).is_err(),
            "a table that is not in editable"
        );
        let round = import_fragment(&keybindings_fragment(&cur, &ed).unwrap(), &cur, &ed).unwrap();
        assert_eq!(round, cur);
        let one = import_fragment(
            "[keybindings.global.controller.system_layer]\nBTN_WEST = \"exec:foot\"\n",
            &cur,
            &ed,
        )
        .unwrap();
        assert_eq!(one.bindings, cur.bindings);
        assert_eq!(
            one.system_layer.get("BTN_WEST").map(String::as_str),
            Some("exec:foot")
        );
    }

    #[test]
    fn vendor_entries_are_not_removable_from_the_user_tier() {
        let (_d, l) = layers("");
        assert!(!l.removable_button("BTN_SOUTH"));
        assert!(l.removable_button("BTN_WEST"));
        assert!(!l.removable_room("BTN_EAST"));
        assert!(l.removable_room("BTN_WEST"));
    }

    #[test]
    fn without_paths_ignores_only_the_listed_leaves() {
        let v: Value =
            "[keybindings]\ntmux_prefix = \"C-a\"\n[keybindings.global]\nbindings = []\n"
                .parse()
                .unwrap();
        let w = without_paths(&v, &[GLOBAL_BINDINGS.to_string()]);
        assert_eq!(
            at(&w, "keybindings.tmux_prefix").and_then(Value::as_str),
            Some("C-a"),
            "a sibling under [keybindings] is protected"
        );
        assert!(at(&w, "keybindings.global").is_none());
    }
}
