// AI-hint: Native dotfiles.projection stage (`mios-gen sync-dotfiles`): syncs the .dotfiles VS Code / code-server settings SSOT to its byte copies, the skeleton and HOME profiles, renders the [theme.edge] and [keybindings] keys into the sources, and merges the ADR-0024 client-portable subset plus the [dotfiles.devcontainer]/[workspace] owned keys into every devcontainer.json / *.code-workspace -- the Rust port of tools/sync-dotfiles.py, byte- and message-identical to it.
// AI-related: tools/sync-dotfiles.py, tools/test_sync-dotfiles.py, tests/test-sync-dotfiles-native-parity.py, usr/share/mios/mios.toml [dotfiles.vscode] [dotfiles.devcontainer] [workspace] [theme.edge], automation/98-drift-checks.sh (check_dotfiles_projection), tools/native/mios-gen/src/pyfmt.rs
// AI-functions: run, Outcome, devcontainer_projection, project_edge_settings, load_policy, project_json_merges, write_atomic
//
// The Python adapter is the reference leg: tools/test_sync-dotfiles.py runs
// against both, and tests/test-sync-dotfiles-native-parity.py diffs the trees
// and output both legs leave. Messages quote values the way Python's repr()
// does (pyfmt), and comparisons are Python's == (1 == 1.0 == True, dicts
// unordered), because a rewrite decision taken on a different equality writes
// different bytes.
//
// One deliberate difference: the adapter read its sources and SSOT from
// MIOS_ROOT (else its own checkout) but wrote byte copies under --root; here
// --root is the one tree for both.

use crate::pyfmt::{dumps, json_string, py_eq, repr};
use serde_json::{Map, Value};
use std::collections::HashMap;
use std::fs;
use std::path::{Component, Path, PathBuf};

const EXIT_DRIFT: u8 = 1;
const EXIT_MISSING_SOURCE: u8 = 2;
const EXIT_BAD_POLICY: u8 = 3;

const VSCODE_SETTINGS: &str = ".dotfiles/vscode/settings.json";
const CODESERVER_SETTINGS: &str = ".dotfiles/code-server/settings.json";
const KEYBINDINGS: &str = "usr/share/mios/keybindings/vscode-settings.json";
const STALE_CSS_COPIES: [&str; 2] = [
    ".dotfiles/code-server/code-server-terminal.css",
    "usr/share/mios/dotfiles/code-server/code-server-terminal.css",
];
/// Byte copies: settings FILES a client reads keep the FULL profile (ADR-0024).
const TARGET_PROJECTIONS: [(&str, &str); 5] = [
    (VSCODE_SETTINGS, "etc/skel/.vscode/settings.json"),
    (VSCODE_SETTINGS, "etc/skel/.config/Code/User/settings.json"),
    (VSCODE_SETTINGS, ".vscode/settings.json"),
    (CODESERVER_SETTINGS, "etc/skel/.local/share/code-server/User/settings.json"),
    (CODESERVER_SETTINGS, "usr/share/mios/agents/code-server-mobile-settings.json"),
];
const HOME_TARGETS: [(&str, &str); 8] = [
    (VSCODE_SETTINGS, ".config/Code/User/settings.json"),
    (VSCODE_SETTINGS, ".vscode-server/data/Machine/settings.json"),
    (VSCODE_SETTINGS, ".vscode-server/data/User/settings.json"),
    (VSCODE_SETTINGS, ".vscode-remote/data/Machine/settings.json"),
    (VSCODE_SETTINGS, ".vscode-remote/data/User/settings.json"),
    (VSCODE_SETTINGS, ".vscode-server-insiders/data/Machine/settings.json"),
    (VSCODE_SETTINGS, ".vscode-server-insiders/data/User/settings.json"),
    (CODESERVER_SETTINGS, ".local/share/code-server/User/settings.json"),
];
const THEME_EXTENSION: &str = "usr/share/mios/extensions/mios-theme-mobile";
const SKEL_EXTENSION_TARGETS: [&str; 2] = [
    "etc/skel/.vscode/extensions/mios-theme-mobile",
    "etc/skel/.local/share/code-server/extensions/mios-theme-mobile",
];
const HOME_EXTENSION_TARGETS: [&str; 5] = [
    ".vscode-server/extensions/mios-theme-mobile",
    ".vscode-server-insiders/extensions/mios-theme-mobile",
    ".vscode-remote/extensions/mios-theme-mobile",
    ".vscode-remote-insiders/extensions/mios-theme-mobile",
    ".local/share/code-server/extensions/mios-theme-mobile",
];
const POLICY_LISTS: [&str; 5] = [
    "desktop_only_keys",
    "user_only_keys",
    "unregistered_keys",
    "client_portable_surfaces",
    "bootstrap_client_portable_surfaces",
];
const POLICY_REQUIRED: [&str; 3] = ["desktop_only_keys", "unregistered_keys", "client_portable_surfaces"];
/// [theme.edge] key -> the settings key it owns in both .dotfiles sources.
const EDGE_SETTINGS: [(&str, &str); 2] = [
    ("code_server_density", "window.density.layout"),
    ("code_server_modern_ui", "workbench.experimental.modernUI"),
];

/// What a run printed and how it exited, exactly as the adapter would have.
pub struct Outcome {
    pub code: u8,
    pub stdout: String,
    pub stderr: String,
}

struct Fatal(u8, String);

fn fatal<T>(code: u8, msg: impl Into<String>) -> Result<T, Fatal> {
    Err(Fatal(code, msg.into()))
}

fn io<E: std::fmt::Display>(what: &Path) -> impl FnOnce(E) -> Fatal + '_ {
    move |e| Fatal(1, format!("{}: {e}", what.display()))
}

type Drift = Vec<(String, String)>;

struct Ctx {
    root: PathBuf,
    bootstrap: PathBuf,
    check: bool,
    client_surfaces: bool,
    home: Option<PathBuf>,
}

/// os.path.normpath(os.path.abspath(p)), lexically: no symlink is resolved.
fn normpath(p: &Path) -> PathBuf {
    let abs = if p.is_absolute() {
        p.to_path_buf()
    } else {
        std::env::current_dir().unwrap_or_default().join(p)
    };
    let mut out = PathBuf::new();
    for part in abs.components() {
        match part {
            Component::ParentDir => {
                out.pop();
            }
            Component::CurDir => {}
            other => out.push(other.as_os_str()),
        }
    }
    out
}

fn read_json(path: &Path) -> Result<Value, Fatal> {
    let text = fs::read_to_string(path).map_err(io(path))?;
    serde_json::from_str(&text).map_err(|e| Fatal(1, format!("{}: {e}", path.display())))
}

// ---- the SSOT ------------------------------------------------------------------

/// TOML -> the object tomllib would hand the adapter (tables in key order of
/// the merged tree; the one table whose order reaches an output, host
/// requirements, is re-ordered from the layers by `ordered_keys`).
fn to_json(v: &toml::Value) -> Value {
    match v {
        toml::Value::String(s) => Value::String(s.clone()),
        toml::Value::Integer(i) => Value::from(*i),
        toml::Value::Float(f) => serde_json::Number::from_f64(*f).map_or(Value::Null, Value::Number),
        toml::Value::Boolean(b) => Value::Bool(*b),
        toml::Value::Datetime(d) => Value::String(d.to_string()),
        toml::Value::Array(items) => Value::Array(items.iter().map(to_json).collect()),
        toml::Value::Table(t) => Value::Object(t.iter().map(|(k, v)| (k.clone(), to_json(v))).collect()),
    }
}

/// Python truthiness of a TOML value.
fn truthy(v: Option<&toml::Value>) -> bool {
    match v {
        None => false,
        Some(toml::Value::String(s)) => !s.is_empty(),
        Some(toml::Value::Integer(i)) => *i != 0,
        Some(toml::Value::Float(f)) => *f != 0.0,
        Some(toml::Value::Boolean(b)) => *b,
        Some(toml::Value::Datetime(_)) => true,
        Some(toml::Value::Array(a)) => !a.is_empty(),
        Some(toml::Value::Table(t)) => !t.is_empty(),
    }
}

/// `str(v)` of a TOML scalar.
fn py_str(v: &toml::Value) -> String {
    match v {
        toml::Value::String(s) => s.clone(),
        other => repr(&to_json(other)),
    }
}

struct Ssot {
    merged: toml::Value,
    layers: Vec<PathBuf>,
}

impl Ssot {
    /// The vendor SSOT and its usr/lib/mios/mios.d fragments, as the gate reads them (Law 13).
    fn load(root: &Path) -> Result<Self, Fatal> {
        let vendor = root.join("usr/share/mios/mios.toml");
        if !vendor.is_file() {
            return fatal(EXIT_MISSING_SOURCE, format!("vendor SSOT missing: {}", vendor.display()));
        }
        let mut layers = vec![vendor];
        layers.extend(mios_resolver::layers::fragments(&root.join("usr/lib/mios/mios.d")));
        let mut merged =
            mios_resolver::layers::merge_layer_files(&layers).map_err(|e| Fatal(1, e.to_string()))?;
        mios_resolver::ports::derive_ports(&mut merged);
        Ok(Ssot { merged, layers })
    }

    fn section(&self, dotted: &str) -> toml::Table {
        let mut cur = &self.merged;
        for part in dotted.split('.') {
            match cur.get(part) {
                Some(next) => cur = next,
                None => return toml::Table::new(),
            }
        }
        cur.as_table().cloned().unwrap_or_default()
    }

    /// The key order Python's deep_merge gives table `dotted`: first
    /// appearance across the layers, in file order.
    fn ordered_keys(&self, dotted: &[&str]) -> Vec<String> {
        let mut keys: Vec<String> = Vec::new();
        for layer in &self.layers {
            let Ok(text) = fs::read_to_string(layer) else { continue };
            let Ok(doc) = text.parse::<toml_edit::Document>() else { continue };
            let mut item = doc.as_item();
            let mut found = true;
            for part in dotted {
                match item.get(part) {
                    Some(next) => item = next,
                    None => {
                        found = false;
                        break;
                    }
                }
            }
            if !found {
                continue;
            }
            let names: Vec<String> = match item {
                toml_edit::Item::Table(t) => t.iter().map(|(k, _)| k.to_string()).collect(),
                toml_edit::Item::Value(toml_edit::Value::InlineTable(t)) => t.iter().map(|(k, _)| k.to_string()).collect(),
                _ => Vec::new(),
            };
            for name in names {
                if !keys.contains(&name) {
                    keys.push(name);
                }
            }
        }
        keys
    }
}

fn name_list(dc: &toml::Table, key: &str, what: &str) -> Result<Vec<String>, Fatal> {
    let names: Option<Vec<String>> = dc.get(key).and_then(|v| v.as_array()).and_then(|items| {
        items.iter().map(|x| x.as_str().filter(|s| !s.is_empty()).map(String::from)).collect()
    });
    match names {
        Some(names) if !names.is_empty() => Ok(names),
        _ => fatal(
            EXIT_BAD_POLICY,
            format!("mios.toml [dotfiles.devcontainer].{key} must be a non-empty list of {what}"),
        ),
    }
}

/// What every devcontainer.json owns, as the resolver emits it.
struct DcOwned {
    forward_ports: Value,
    container_env: Vec<(String, String)>,
    remote_user: Value,
    host_requirements: Value,
    workspace_folder: Value,
    workspace_mount: Value,
    workspace_devcontainer: Option<String>,
    folders: Value,
}

fn full_match(pattern: &str, s: &str) -> bool {
    regex::Regex::new(&format!("^(?:{pattern})$")).is_ok_and(|re| re.is_match(s))
}

fn devcontainer_projection(ssot: &Ssot) -> Result<DcOwned, Fatal> {
    let dc = ssot.section("dotfiles.devcontainer");
    let keys = name_list(&dc, "forward_port_keys", "[ports] key names")?;
    let mut env_names: Vec<String> = Vec::new();
    for name in name_list(&dc, "container_env_keys", "emitted MIOS_* names")? {
        if !env_names.contains(&name) {
            env_names.push(name);
        }
    }
    let exports =
        mios_resolver::emit_shell::shell_exports(&ssot.merged, mios_resolver::stack_offset_of(&ssot.merged));
    let mut ports = Vec::new();
    for key in &keys {
        let value = exports.get(&format!("MIOS_PORTS_{}", key.to_uppercase())).map_or("", String::as_str);
        if value.is_empty() || !value.chars().all(|c| c.is_ascii_digit()) {
            return fatal(
                EXIT_BAD_POLICY,
                format!(
                    "mios.toml [dotfiles.devcontainer].forward_port_keys names {}, which is not an integer [ports] key",
                    repr(&Value::from(key.as_str()))
                ),
            );
        }
        ports.push(Value::from(value.parse::<u64>().map_err(|e| Fatal(EXIT_BAD_POLICY, e.to_string()))?));
    }
    let mut container_env = Vec::new();
    for name in &env_names {
        let value = exports.get(name);
        match value {
            Some(v) if !v.is_empty() && !v.contains("${") => container_env.push((name.clone(), v.clone())),
            _ => {
                let shown = value.map_or(Value::Null, |v| Value::from(v.as_str()));
                return fatal(
                    EXIT_BAD_POLICY,
                    format!(
                        "mios.toml [dotfiles.devcontainer].container_env_keys names {}, which the resolver does not emit as a resolved value ({})",
                        repr(&Value::from(name.as_str())),
                        repr(&shown)
                    ),
                );
            }
        }
    }
    let ws = ssot.section("workspace");
    let repos = ws.get("repos").and_then(|v| v.as_array());
    let primary = ws.get("primary");
    let root_str = if truthy(ws.get("root")) { py_str(ws.get("root").unwrap()) } else { String::new() };
    let repos_ok = repos.is_some_and(|repos| {
        !repos.is_empty()
            && repos.iter().all(|r| r.as_table().is_some_and(|t| truthy(t.get("name")) && truthy(t.get("url"))))
            && primary.is_some_and(|p| repos.iter().any(|r| r.get("name") == Some(p)))
    });
    if !repos_ok || !root_str.starts_with('/') || !truthy(ws.get("devcontainer")) {
        return fatal(
            EXIT_BAD_POLICY,
            "mios.toml [workspace] needs an absolute root, a devcontainer path and a non-empty repos list of {name, url} that names primary",
        );
    }
    let primary = primary.unwrap();
    let folders: Vec<Value> = repos
        .unwrap()
        .iter()
        .map(|r| {
            let name = r.get("name").unwrap();
            let label = if truthy(r.get("label")) { r.get("label").unwrap() } else { name };
            let path = if name == primary { ".".to_string() } else { format!("../{}", py_str(name)) };
            let mut folder = Map::new();
            folder.insert("name".into(), to_json(label));
            folder.insert("path".into(), Value::String(path));
            Value::Object(folder)
        })
        .collect();
    let identity = ssot.section("identity");
    let user = identity.get("username");
    let user_ok = user.and_then(|u| u.as_str()).is_some_and(|u| full_match("[a-z_][a-z0-9_-]*", u));
    if !user_ok {
        return fatal(
            EXIT_BAD_POLICY,
            format!(
                "mios.toml [identity].username {} is not a user name the devcontainer can run as",
                user.map_or("None".into(), |u| repr(&to_json(u)))
            ),
        );
    }
    let host = dc.get("host_requirements").and_then(|v| v.as_table());
    let host_ok = host.is_some_and(|h| {
        let mut names: Vec<&str> = h.keys().map(String::as_str).collect();
        names.sort_unstable();
        names == ["cpus", "memory", "storage"]
            && h["cpus"].as_integer().is_some_and(|c| c >= 1)
            && ["memory", "storage"]
                .iter()
                .all(|k| h[*k].as_str().is_some_and(|s| full_match("[1-9][0-9]*gb", s)))
    });
    if !host_ok {
        return fatal(
            EXIT_BAD_POLICY,
            "mios.toml [dotfiles.devcontainer].host_requirements must be { cpus = <int>, memory = \"<n>gb\", storage = \"<n>gb\" }",
        );
    }
    let host = host.unwrap();
    let mut order = ssot.ordered_keys(&["dotfiles", "devcontainer", "host_requirements"]);
    order.retain(|k| host.contains_key(k));
    for k in host.keys() {
        if !order.contains(k) {
            order.push(k.clone());
        }
    }
    let host_requirements: Map<String, Value> = order.iter().map(|k| (k.clone(), to_json(&host[k]))).collect();
    Ok(DcOwned {
        forward_ports: Value::Array(ports),
        container_env,
        remote_user: to_json(user.unwrap()),
        host_requirements: Value::Object(host_requirements),
        workspace_folder: to_json(ws.get("root").unwrap()),
        workspace_mount: Value::String(format!(
            "source=${{localWorkspaceFolder}},target={}/{},type=bind",
            py_str(ws.get("root").unwrap()),
            py_str(primary)
        )),
        workspace_devcontainer: ws.get("devcontainer").and_then(|v| v.as_str()).map(String::from),
        folders: Value::Array(folders),
    })
}

/// Same Python type: `type(a) is type(b)` over the objects json/tomllib build.
fn same_type(a: &Value, b: &Value) -> bool {
    match (a, b) {
        (Value::Number(x), Value::Number(y)) => x.is_f64() == y.is_f64(),
        _ => std::mem::discriminant(a) == std::mem::discriminant(b),
    }
}

fn rel_label(root: &Path, path: &Path) -> String {
    path.strip_prefix(root).unwrap_or(path).to_string_lossy().replace('\\', "/")
}

/// Render the [theme.edge] keys into each .dotfiles settings source in place.
fn project_edge_settings(ctx: &Ctx, ssot: &Ssot) -> Result<Drift, Fatal> {
    let edge = ssot.section("theme.edge");
    let missing: Vec<&str> = EDGE_SETTINGS.iter().map(|(t, _)| *t).filter(|t| !edge.contains_key(*t)).collect();
    if !missing.is_empty() {
        return fatal(
            EXIT_BAD_POLICY,
            format!(
                "mios.toml [theme.edge] lacks {}, so the settings they own cannot be projected",
                missing.join(", ")
            ),
        );
    }
    let mut drift = Drift::new();
    for src in [VSCODE_SETTINGS, CODESERVER_SETTINGS] {
        let path = ctx.root.join(src);
        let label = rel_label(&ctx.root, &path);
        let text = fs::read_to_string(&path).map_err(io(&path))?;
        let doc: Value = serde_json::from_str(&text).map_err(|e| Fatal(1, format!("{}: {e}", path.display())))?;
        let mut new = text.clone();
        for (tkey, skey) in EDGE_SETTINGS {
            let want = to_json(&edge[tkey]);
            if let Some(have) = doc.get(skey) {
                if py_eq(have, &want) && same_type(have, &want) {
                    continue;
                }
            }
            drift.push((
                label.clone(),
                format!(
                    "{skey} is {}, mios.toml [theme.edge].{tkey} renders {}",
                    doc.get(skey).map_or("'<absent>'".into(), repr),
                    repr(&want)
                ),
            ));
            let pattern = format!(
                r"(?m)^([ \t]*{}[ \t]*:[ \t]*)[^,\n]*?([ \t]*,?[ \t]*)$",
                regex::escape(&json_string(skey))
            );
            let re = regex::Regex::new(&pattern).map_err(|e| Fatal(1, e.to_string()))?;
            let replaced = re.captures(&new).map(|c| {
                let m = c.get(0).unwrap();
                format!("{}{}{}{}{}", &new[..m.start()], &c[1], dumps(&want), &c[2], &new[m.end()..])
            });
            new = match replaced {
                Some(r) => r,
                None => new.replacen("{\n", &format!("{{\n  {}: {},\n", json_string(skey), dumps(&want)), 1),
            };
        }
        if new != text && !ctx.check {
            let mut want_doc = doc.clone();
            if let Value::Object(map) = &mut want_doc {
                for (tkey, skey) in EDGE_SETTINGS {
                    map.insert(skey.into(), to_json(&edge[tkey]));
                }
            }
            let parsed: Option<Value> = serde_json::from_str(&new).ok();
            if !parsed.is_some_and(|p| py_eq(&p, &want_doc)) {
                return fatal(
                    EXIT_BAD_POLICY,
                    format!("{label}: the in-place edit of the [theme.edge] keys did not parse back"),
                );
            }
            write_atomic(&path, &new)?;
        }
    }
    Ok(drift)
}

struct Policy {
    lists: HashMap<&'static str, Vec<String>>,
}

impl Policy {
    fn list(&self, key: &str) -> &[String] {
        self.lists.get(key).map_or(&[], Vec::as_slice)
    }
}

/// [dotfiles.vscode]: exit 3 on an absent or empty partition.
fn load_policy(ssot: &Ssot) -> Result<Policy, Fatal> {
    let pol = ssot.section("dotfiles.vscode");
    let mut lists = HashMap::new();
    for key in POLICY_LISTS {
        let val: Option<Vec<String>> = match pol.get(key) {
            None => Some(Vec::new()),
            Some(toml::Value::Array(items)) => {
                items.iter().map(|x| x.as_str().filter(|s| !s.is_empty()).map(String::from)).collect()
            }
            Some(_) => None,
        };
        match val {
            Some(v) => {
                lists.insert(key, v);
            }
            None => {
                return fatal(
                    EXIT_BAD_POLICY,
                    format!("mios.toml [dotfiles.vscode].{key} must be a list of non-empty strings"),
                )
            }
        }
    }
    for key in POLICY_REQUIRED {
        if lists[key].is_empty() {
            return fatal(
                EXIT_BAD_POLICY,
                format!(
                    "mios.toml [dotfiles.vscode].{key} is empty or absent -- the client-portable partition (ADR-0024) cannot be projected without it"
                ),
            );
        }
    }
    Ok(Policy { lists })
}

/// Every key that must not reach a client-portable surface, tagged with why.
fn pruned_keys(pol: &Policy) -> HashMap<String, &'static str> {
    let mut out = HashMap::new();
    for (list, why) in [
        ("unregistered_keys", "unregistered"),
        ("user_only_keys", "User-settings-only (APPLICATION scope)"),
        ("desktop_only_keys", "desktop-only"),
    ] {
        for key in pol.list(list) {
            out.insert(key.clone(), why);
        }
    }
    out
}

fn surface_key_path(rel: &str) -> Result<&'static [&'static str], Fatal> {
    if rel.ends_with(".code-workspace") {
        return Ok(&["settings"]);
    }
    if Path::new(rel).file_name().is_some_and(|n| n == "devcontainer.json") {
        return Ok(&["customizations", "vscode", "settings"]);
    }
    fatal(
        EXIT_BAD_POLICY,
        format!(
            "[dotfiles.vscode] names a surface of unknown type: {rel} (only devcontainer.json and *.code-workspace carry an API-applied settings block)"
        ),
    )
}

fn base_name(root: &Path) -> String {
    root.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default()
}

fn ssot_unregistered(pol: &Policy, settings: &Value, label: &str) -> Drift {
    pol.list("unregistered_keys")
        .iter()
        .filter(|k| settings.get(k.as_str()).is_some())
        .map(|k| {
            (
                label.to_string(),
                format!(
                    "unregistered key {k} is still in the SSOT -- delete it (or drop it from [dotfiles.vscode].unregistered_keys if it came back upstream)"
                ),
            )
        })
        .collect()
}

fn get_or_none(doc: &Value, key: &str) -> Value {
    doc.get(key).cloned().unwrap_or(Value::Null)
}

/// Merge the client-portable subset into every surface and prune what a
/// connecting client may not register; own the devcontainer keys.
fn project_json_merges(ctx: &Ctx, pol: &Policy, ssot_settings: &Value, dc: &DcOwned) -> Result<Drift, Fatal> {
    let pruned = pruned_keys(pol);
    let empty = Map::new();
    let portable: Map<String, Value> = ssot_settings
        .as_object()
        .unwrap_or(&empty)
        .iter()
        .filter(|(k, _)| !pruned.contains_key(*k))
        .map(|(k, v)| (k.clone(), v.clone()))
        .collect();
    let mut surfaces = Vec::new();
    for (root, list) in [(&ctx.root, "client_portable_surfaces"), (&ctx.bootstrap, "bootstrap_client_portable_surfaces")] {
        for rel in pol.list(list) {
            surfaces.push((root, rel.clone(), surface_key_path(rel)?, format!("{}/{rel}", base_name(root))));
        }
    }
    let mut drift = Drift::new();
    for (repo_root, rel, key_path, label) in surfaces {
        let is_repo = *repo_root == ctx.root;
        let dst = repo_root.join(&rel);
        if !dst.is_file() {
            if is_repo {
                drift.push((label, "client-portable surface named by [dotfiles.vscode] is missing".into()));
            }
            continue; // else: degrade open -- the sibling repo is not checked out here
        }
        let mut doc = read_json(&dst)?;
        // _get_in: setdefault every parent, as the adapter did, before reading.
        let (parents, last) = key_path.split_at(key_path.len() - 1);
        {
            let mut cur = &mut doc;
            for key in parents {
                let map = cur.as_object_mut().ok_or_else(|| Fatal(1, format!("{}: not an object", dst.display())))?;
                cur = map.entry(key.to_string()).or_insert_with(|| Value::Object(Map::new()));
            }
            if !cur.is_object() {
                return fatal(1, format!("{}: {} is not an object", dst.display(), parents.join(".")));
            }
        }
        let parent_of = |doc: &Value| -> Value {
            let mut cur = doc;
            for key in parents {
                cur = &cur[*key];
            }
            cur.clone()
        };
        let existing = parent_of(&doc).get(last[0]).cloned().unwrap_or_else(|| Value::Object(Map::new()));
        let Some(existing) = existing.as_object().cloned() else {
            drift.push((label, format!("settings block at {} is not an object", key_path.join("."))));
            continue;
        };
        let mut expected: Map<String, Value> =
            existing.iter().filter(|(k, _)| !pruned.contains_key(*k)).map(|(k, v)| (k.clone(), v.clone())).collect();
        for (k, v) in &portable {
            expected.insert(k.clone(), v.clone());
        }
        let mut reasons: Vec<String> = existing
            .keys()
            .filter_map(|k| pruned.get(k).map(|why| format!("{why} key {k} must not reach a client-portable surface (ADR-0024)")))
            .collect();
        for (k, v) in &portable {
            match existing.get(k) {
                None => reasons.push(format!("SSOT key {k} is missing from the surface")),
                Some(have) if !py_eq(have, v) => reasons.push(format!("SSOT key {k} differs from the SSOT value")),
                _ => {}
            }
        }
        let block_stale = !py_eq(&Value::Object(expected.clone()), &Value::Object(existing.clone()));
        if block_stale && reasons.is_empty() {
            reasons.push("settings block differs from the SSOT projection".into());
        }
        let mut owned_stale = false;
        let top = doc.as_object_mut().ok_or_else(|| Fatal(1, format!("{}: not an object", dst.display())))?;
        if Path::new(&rel).file_name().is_some_and(|n| n == "devcontainer.json") {
            let have_fwd = top.get("forwardPorts").cloned().unwrap_or(Value::Null);
            let fwd_stale = !py_eq(&have_fwd, &dc.forward_ports);
            if fwd_stale {
                reasons.push(format!(
                    "forwardPorts {} differs from the [dotfiles.devcontainer].forward_port_keys projection {}",
                    repr(&have_fwd),
                    repr(&dc.forward_ports)
                ));
            }
            let cenv = top.get("containerEnv").and_then(Value::as_object).cloned().unwrap_or_default();
            let mut env_stale = false;
            for (n, v) in &dc.container_env {
                let have = cenv.get(n).cloned().unwrap_or(Value::Null);
                if !py_eq(&have, &Value::from(v.as_str())) {
                    env_stale = true;
                    reasons.push(format!(
                        "containerEnv.{n} {} differs from the resolved SSOT value {} ([dotfiles.devcontainer].container_env_keys)",
                        repr(&have),
                        repr(&Value::from(v.as_str()))
                    ));
                }
            }
            owned_stale = fwd_stale || env_stale;
            for (key, source, want) in [
                ("remoteUser", "[identity].username", &dc.remote_user),
                ("hostRequirements", "[dotfiles.devcontainer].host_requirements", &dc.host_requirements),
            ] {
                let have = top.get(key).cloned().unwrap_or(Value::Null);
                if !py_eq(&have, want) {
                    reasons.push(format!("{key} {} differs from the {source} projection {}", repr(&have), repr(want)));
                    owned_stale = true;
                }
            }
            if owned_stale {
                top.insert("forwardPorts".into(), dc.forward_ports.clone());
                let mut env = cenv.clone();
                for (n, v) in &dc.container_env {
                    env.insert(n.clone(), Value::from(v.as_str()));
                }
                top.insert("containerEnv".into(), Value::Object(env));
                top.insert("remoteUser".into(), dc.remote_user.clone());
                top.insert("hostRequirements".into(), dc.host_requirements.clone());
            }
        }
        if is_repo && dc.workspace_devcontainer.as_deref() == Some(rel.as_str()) {
            for (key, want) in [("workspaceFolder", &dc.workspace_folder), ("workspaceMount", &dc.workspace_mount)] {
                let have = top.get(key).cloned().unwrap_or(Value::Null);
                if !py_eq(&have, want) {
                    reasons.push(format!("{key} {} differs from the [workspace] projection {}", repr(&have), repr(want)));
                    top.insert(key.into(), want.clone());
                    owned_stale = true;
                }
            }
        }
        if is_repo && rel.ends_with(".code-workspace") && !py_eq(&get_or_none(&Value::Object(top.clone()), "folders"), &dc.folders) {
            reasons.push("folders differ from the [workspace].repos projection".into());
            top.insert("folders".into(), dc.folders.clone());
            owned_stale = true;
        }
        drift.extend(reasons.into_iter().map(|r| (label.clone(), r)));
        if !ctx.check && (block_stale || owned_stale) {
            let mut cur = &mut doc;
            for key in parents {
                cur = &mut cur[*key];
            }
            cur.as_object_mut().unwrap().insert(last[0].to_string(), Value::Object(expected));
            write_atomic(&dst, &(dumps(&doc) + "\n"))?;
        }
    }
    Ok(drift)
}

// ---- writes --------------------------------------------------------------------

#[cfg(unix)]
fn new_file_mode() -> u32 {
    // 0o666 masked by the process umask, read without changing it.
    let mask = fs::read_to_string("/proc/self/status")
        .ok()
        .and_then(|s| s.lines().find_map(|l| l.strip_prefix("Umask:").map(|v| v.trim().to_string())))
        .and_then(|v| u32::from_str_radix(&v, 8).ok())
        .unwrap_or(0o022);
    0o666 & !mask
}

/// Temp file beside the target + rename; the target keeps its own mode, a
/// new one gets what a plain open() would give it.
fn write_atomic(path: &Path, text: &str) -> Result<(), Fatal> {
    use std::io::Write;
    let dir = path.parent().filter(|p| !p.as_os_str().is_empty()).unwrap_or(Path::new("."));
    fs::create_dir_all(dir).map_err(io(dir))?;
    let name = path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
    let mut tmp = tempfile::Builder::new()
        .prefix(&format!(".{name}."))
        .tempfile_in(dir)
        .map_err(io(dir))?;
    tmp.write_all(text.as_bytes()).map_err(io(path))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = fs::metadata(path).map(|m| m.permissions().mode() & 0o7777).unwrap_or_else(|_| new_file_mode());
        fs::set_permissions(tmp.path(), fs::Permissions::from_mode(mode)).map_err(io(path))?;
    }
    tmp.persist(path).map_err(|e| Fatal(1, format!("{}: {}", path.display(), e.error)))?;
    Ok(())
}

/// shutil.copy2: bytes, permission bits and the access/modification times.
fn copy2(src: &Path, dst: &Path) -> std::io::Result<()> {
    fs::copy(src, dst)?;
    let meta = fs::metadata(src)?;
    let times = fs::FileTimes::new().set_accessed(meta.accessed()?).set_modified(meta.modified()?);
    fs::OpenOptions::new().write(true).open(dst)?.set_times(times)
}

/// shutil.copytree(src, dst) with its defaults: symlinks followed, copy2 per file.
fn copytree(src: &Path, dst: &Path) -> std::io::Result<()> {
    fs::create_dir_all(dst)?;
    for entry in fs::read_dir(src)? {
        let entry = entry?;
        let from = entry.path();
        let to = dst.join(entry.file_name());
        if fs::metadata(&from)?.is_dir() {
            copytree(&from, &to)?;
        } else {
            copy2(&from, &to)?;
        }
    }
    fs::set_permissions(dst, fs::metadata(src)?.permissions())
}

/// Replace `target` with a fresh copy of the theme extension, ignoring errors as the adapter did.
fn replace_tree(src: &Path, target: &Path) {
    let _ = (|| -> std::io::Result<()> {
        if let Ok(meta) = fs::symlink_metadata(target) {
            if meta.file_type().is_symlink() {
                fs::remove_file(target)?;
            } else if meta.is_dir() {
                fs::remove_dir_all(target)?;
            }
        }
        copytree(src, target)
    })();
}

fn project_home_and_skel(ctx: &Ctx) {
    let home = ctx.home.as_ref().filter(|h| h.is_dir());
    if let Some(home) = home {
        for (src, rel) in HOME_TARGETS {
            let dst = home.join(rel);
            if dst.parent().is_some_and(Path::is_dir) {
                let _ = copy2(&ctx.root.join(src), &dst);
            }
        }
    }
    let theme = ctx.root.join(THEME_EXTENSION);
    if !theme.is_dir() {
        return;
    }
    for rel in SKEL_EXTENSION_TARGETS {
        let target = ctx.root.join(rel);
        if let Some(parent) = target.parent() {
            let _ = fs::create_dir_all(parent);
        }
        replace_tree(&theme, &target);
    }
    if let Some(home) = home {
        for rel in HOME_EXTENSION_TARGETS {
            let target = home.join(rel);
            if target.parent().is_some_and(Path::is_dir) {
                replace_tree(&theme, &target);
            }
        }
    }
}

// ---- the run -------------------------------------------------------------------

fn sync(ctx: &Ctx) -> Result<Drift, Fatal> {
    for src in [VSCODE_SETTINGS, CODESERVER_SETTINGS] {
        let path = ctx.root.join(src);
        if !path.is_file() {
            return fatal(EXIT_MISSING_SOURCE, format!("Missing SSOT source: {}", path.display()));
        }
    }
    let ssot = Ssot::load(&ctx.root)?;
    let pol = load_policy(&ssot)?;
    let dc = devcontainer_projection(&ssot)?;
    let mut drift = project_edge_settings(ctx, &ssot)?;
    // Terminal shortcuts: both editor profiles pass tmux's prefix through.
    let mobile = read_json(&ctx.root.join(KEYBINDINGS))?;
    for src in [VSCODE_SETTINGS, CODESERVER_SETTINGS] {
        let path = ctx.root.join(src);
        let profile = read_json(&path)?;
        let mut updated = profile.clone();
        if let (Value::Object(map), Value::Object(extra)) = (&mut updated, &mobile) {
            for (k, v) in extra {
                map.insert(k.clone(), v.clone());
            }
        }
        if !py_eq(&updated, &profile) {
            drift.push((
                path.to_string_lossy().into_owned(),
                "terminal shortcut settings differ from [keybindings] projection".into(),
            ));
            if !ctx.check {
                write_atomic(&path, &(dumps(&updated) + "\n"))?;
            }
        }
    }
    let vscode_ssot = read_json(&ctx.root.join(VSCODE_SETTINGS))?;
    let codeserver_ssot = read_json(&ctx.root.join(CODESERVER_SETTINGS))?;
    drift.extend(ssot_unregistered(&pol, &vscode_ssot, VSCODE_SETTINGS));
    drift.extend(ssot_unregistered(&pol, &codeserver_ssot, CODESERVER_SETTINGS));

    if !ctx.client_surfaces {
        for rel in STALE_CSS_COPIES {
            let stale = ctx.root.join(rel);
            if stale.is_file() {
                drift.push((
                    rel.into(),
                    "stale copy of the rendered stylesheet -- the one copy is usr/share/mios/themes/code-server-terminal.css (mios-dotfiles-render)".into(),
                ));
                if !ctx.check {
                    fs::remove_file(&stale).map_err(io(&stale))?;
                }
            }
        }
        for (src, rel) in TARGET_PROJECTIONS {
            let src = ctx.root.join(src);
            let dst = ctx.root.join(rel);
            let bytes = fs::read(&src).map_err(io(&src))?;
            if dst.is_file() {
                if fs::read(&dst).map_err(io(&dst))? != bytes {
                    drift.push((rel.into(), "byte copy differs from the SSOT source".into()));
                }
            } else {
                drift.push((rel.into(), "byte copy is missing".into()));
            }
            if !ctx.check {
                if let Some(parent) = dst.parent() {
                    fs::create_dir_all(parent).map_err(io(parent))?;
                }
                fs::write(&dst, &bytes).map_err(io(&dst))?;
            }
        }
        if !ctx.check {
            project_home_and_skel(ctx);
        }
    }
    drift.extend(project_json_merges(ctx, &pol, &vscode_ssot, &dc)?);
    Ok(drift)
}

/// `mios-gen sync-dotfiles`: `bootstrap` defaults to MIOS_BOOTSTRAP_ROOT, else
/// `<root>/../mios-bootstrap`; `home` is where write mode refreshes the
/// editor profiles a user already has.
pub fn run(root: &Path, bootstrap: Option<&Path>, home: Option<&Path>, check: bool, client_surfaces: bool) -> Outcome {
    let root = normpath(root);
    let bootstrap = normpath(&bootstrap.map(Path::to_path_buf).unwrap_or_else(|| root.join("..").join("mios-bootstrap")));
    let ctx = Ctx { root, bootstrap, check, client_surfaces, home: home.map(Path::to_path_buf) };
    let mut out = Outcome { code: 0, stdout: String::new(), stderr: String::new() };
    let drift = match sync(&ctx) {
        Ok(drift) => drift,
        Err(Fatal(code, msg)) => {
            out.code = code;
            out.stderr = format!("[sync-dotfiles] FATAL: {msg}\n");
            return out;
        }
    };
    if check {
        if drift.is_empty() {
            out.stdout = "[sync-dotfiles] All .dotfiles projections in sync.\n".into();
            return out;
        }
        for (label, reason) in &drift {
            out.stderr.push_str(&format!("[sync-dotfiles] DRIFT {label}: {reason}\n"));
        }
        let mut files: Vec<&str> = drift.iter().map(|(l, _)| l.as_str()).collect();
        files.sort_unstable();
        files.dedup();
        out.stderr.push_str(&format!("[sync-dotfiles] Drift detected in {} files: {}\n", files.len(), files.join(", ")));
        out.code = EXIT_DRIFT;
        return out;
    }
    // The SSOT itself carrying a key VS Code does not know is the one drift
    // the projection cannot repair.
    let unfixable: Vec<_> = drift
        .iter()
        .filter(|(label, reason)| label.starts_with(".dotfiles/") && reason.starts_with("unregistered key"))
        .collect();
    if !unfixable.is_empty() {
        for (label, reason) in unfixable {
            out.stderr.push_str(&format!("[sync-dotfiles] DRIFT {label}: {reason}\n"));
        }
        out.code = EXIT_DRIFT;
        return out;
    }
    let what = if client_surfaces {
        "the client-portable surfaces".to_string()
    } else {
        format!("{} targets + the client-portable surfaces", TARGET_PROJECTIONS.len())
    };
    out.stdout = format!("[sync-dotfiles] Successfully synchronized .dotfiles SSOT to {what}.\n");
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_new_surface_gets_the_umask_mode_and_a_rewrite_keeps_its_own() {
        use std::os::unix::fs::PermissionsExt;
        let dir = tempfile::tempdir().unwrap();
        let fresh = dir.path().join("new/devcontainer.json");
        write_atomic(&fresh, "{}\n").unwrap();
        assert_eq!(fs::metadata(&fresh).unwrap().permissions().mode() & 0o777, new_file_mode());
        assert_ne!(new_file_mode() & 0o600, 0, "a plain open() is never private-only under a sane umask");
        fs::set_permissions(&fresh, fs::Permissions::from_mode(0o755)).unwrap();
        write_atomic(&fresh, "{\"a\": 1}\n").unwrap();
        assert_eq!(fs::metadata(&fresh).unwrap().permissions().mode() & 0o777, 0o755);
        assert_eq!(fs::read_to_string(&fresh).unwrap(), "{\"a\": 1}\n");
        let leftovers: Vec<_> = fs::read_dir(fresh.parent().unwrap())
            .unwrap()
            .filter_map(|e| e.ok())
            .filter(|e| e.file_name().to_string_lossy().starts_with(".devcontainer.json."))
            .collect();
        assert!(leftovers.is_empty());
    }

    #[test]
    fn normpath_is_lexical() {
        assert_eq!(normpath(Path::new("/a/b/../c/./d")), PathBuf::from("/a/c/d"));
    }

    #[test]
    fn a_missing_source_is_exit_2_and_writes_nothing() {
        let dir = tempfile::tempdir().unwrap();
        let out = run(dir.path(), None, None, false, false);
        assert_eq!(out.code, EXIT_MISSING_SOURCE);
        assert!(out.stderr.starts_with("[sync-dotfiles] FATAL: Missing SSOT source: "), "{}", out.stderr);
        assert_eq!(fs::read_dir(dir.path()).unwrap().count(), 0);
    }
}
