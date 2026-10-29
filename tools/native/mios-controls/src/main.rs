// AI-hint: Main entrypoint for mios-controls: with no verb (on a terminal) the binding-editor TUI; verbs list/check/set/add/remove/pad/restore-defaults/export/import/devices drive the same draft -> validate -> apply pipeline headlessly (dry-run unless --apply).
// AI-related: usr/share/mios/mios.toml [keybindings.global] [keybindings.global.controls], tools/native/Cargo.toml, src/mios-rs/mios-gate/src/keybinds.rs, tests/test-mios-controls.py

#![forbid(unsafe_code)]

mod apply;
mod devices;
mod input;
mod model;
mod tui;
mod validate;

/// The gate's keybind-collision analysis, compiled from its one source file so the editor and `mios-gate
/// keybind-collisions` can never disagree about what collides.
#[allow(dead_code)]
#[path = "../../../../src/mios-rs/mios-gate/src/keybinds.rs"]
mod keybinds;

use model::{at, field, Draft, Layers};
use std::io::{IsTerminal, Write};
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use toml::Value;

/// The report shape keybinds.rs builds for the gate (crate::Report there).
#[allow(dead_code)]
pub struct Report {
    pub check: String,
    pub ok: bool,
    pub could_not_run: Option<String>,
    pub summary: String,
    pub findings: Vec<String>,
}

/// [keybindings.global.controls] string tunable.
pub fn controls_str(merged: &Value, key: &str, fallback: &str) -> String {
    at(merged, &format!("keybindings.global.controls.{key}")).and_then(Value::as_str).filter(|s| !s.is_empty()).unwrap_or(fallback).to_owned()
}

/// [keybindings.global.controls] integer tunable (absent or negative = automatic).
pub fn controls_int(merged: &Value, key: &str) -> Option<i64> {
    at(merged, &format!("keybindings.global.controls.{key}")).and_then(Value::as_integer).filter(|v| *v >= 0)
}

/// Create a new private file; never overwrite an existing one.
pub fn write_private_new(path: &Path, text: &str) -> Result<(), String> {
    let mut f = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(path)
        .map_err(|e| format!("{}: {e}", path.display()))?;
    f.write_all(text.as_bytes()).map_err(|e| format!("{}: {e}", path.display()))
}

const USAGE: &str = "Usage: mios-controls [--root DIR] [VERB]
  (no verb)                 the binding editor TUI (needs a terminal)
  list [--json]             effective bindings and the controller system layer
  check                     validate the current map; exit 1 on findings
  devices [--json]          connected keyboards and controllers (read-only)
  set ID [--chord C] [--action A] [--contexts a,b] [--passthrough a,b] [--bypass C] [--tier N] [--apply]
  add ID --chord C --action A [--tier N] [--contexts a,b] [--passthrough a,b] [--apply]
  remove ID [--apply]
  pad BUTTON ACTION [--apply]   Guide + BUTTON system-layer action
  restore-defaults [--apply]    drop your [keybindings.global] overrides
  export FILE                   write the binding map alone to a new private file
  import FILE [--apply]         read a binding map exported by this tool
Every editing verb validates the draft and prints the user-tier [keybindings] it would write; --apply writes
it (atomically, 0600, previous tier kept) only when the draft adds no findings.";

struct Args {
    root: PathBuf,
    verb: Option<String>,
    pos: Vec<String>,
    opts: Vec<(String, String)>,
    apply: bool,
    json: bool,
}

fn parse_args(raw: Vec<String>) -> Result<Args, String> {
    let mut a = Args { root: PathBuf::from("/"), verb: None, pos: Vec::new(), opts: Vec::new(), apply: false, json: false };
    let mut it = raw.into_iter();
    while let Some(x) = it.next() {
        match x.as_str() {
            "-h" | "--help" => return Err(String::new()),
            "--root" => a.root = PathBuf::from(it.next().ok_or("--root needs a directory")?),
            "--apply" => a.apply = true,
            "--json" => a.json = true,
            o if o.starts_with("--") => {
                let v = it.next().ok_or_else(|| format!("{o} needs a value"))?;
                a.opts.push((o.trim_start_matches("--").to_owned(), v));
            }
            _ if a.verb.is_none() => a.verb = Some(x),
            _ => a.pos.push(x),
        }
    }
    Ok(a)
}

fn opt<'a>(a: &'a Args, k: &str) -> Option<&'a str> {
    a.opts.iter().rev().find(|(n, _)| n == k).map(|(_, v)| v.as_str())
}

fn list_value(v: &str) -> Value {
    Value::Array(v.split(',').map(str::trim).filter(|s| !s.is_empty()).map(|s| Value::String(s.into())).collect())
}

fn edit_binding(b: &mut toml::Table, a: &Args, sep: &str) -> Result<(), String> {
    for key in ["chord", "bypass"] {
        if let Some(v) = opt(a, key) {
            if v.is_empty() && key == "bypass" {
                b.remove("bypass");
                continue;
            }
            mios_resolver::keybindings::normalize_chord(v, sep).map_err(|e| format!("{key} {v:?}: {e}"))?;
            b.insert(key.into(), Value::String(v.into()));
        }
    }
    if let Some(v) = opt(a, "action") {
        b.insert("action".into(), Value::String(v.into()));
    }
    for key in ["contexts", "passthrough"] {
        if let Some(v) = opt(a, key) {
            if v.is_empty() && key == "contexts" {
                b.remove("contexts");
            } else {
                b.insert(key.into(), list_value(v));
            }
        }
    }
    if let Some(v) = opt(a, "tier") {
        b.insert("tier".into(), Value::Integer(v.parse().map_err(|_| "--tier must be an integer")?));
    }
    Ok(())
}

/// Validate a draft, print what it would write, and apply it when asked and clean.
fn finish(layers: &Layers, draft: &Draft, apply: bool) -> Result<ExitCode, String> {
    let merged = layers.effective();
    let validator = validate::Validator::from_ssot(&merged);
    let doc = layers.user_doc_for(draft)?;
    let text = doc.to_string();
    let outcome = validator.run(layers, &text)?;
    let new = outcome.new_findings();
    // Print only the [keybindings] part: the user tier also holds credentials.
    let preview = model::parse_value(&text)?.get("keybindings").cloned();
    match preview {
        Some(kb) => {
            let mut t = toml::Table::new();
            t.insert("keybindings".into(), kb);
            println!("# user-tier [keybindings] after this change ({}):\n{}", layers.user_path.display(), toml::to_string(&t).map_err(|e| e.to_string())?);
        }
        None => println!("# user tier carries no [keybindings] after this change ({})", layers.user_path.display()),
    }
    println!("validation: {}", outcome.summary);
    for f in &new {
        println!("NEW FINDING: {f}");
    }
    if !new.is_empty() {
        return Ok(ExitCode::from(1));
    }
    if text == layers.user_text {
        println!("no change to apply");
        return Ok(ExitCode::SUCCESS);
    }
    if !apply {
        println!("dry run: pass --apply to write it");
        return Ok(ExitCode::SUCCESS);
    }
    let suffix = controls_str(&merged, "backup_suffix", "controls-backup");
    let backup = apply::apply(layers, &text, &suffix)?;
    println!("applied to {}{}", layers.user_path.display(), backup.map(|b| format!(" (previous tier: {})", b.display())).unwrap_or_default());
    println!("the compositor picks it up on its next configuration reload");
    Ok(ExitCode::SUCCESS)
}

fn run(a: Args) -> Result<ExitCode, String> {
    if a.verb.as_deref() == Some("devices") {
        let d = devices::scan();
        if a.json {
            let v: Vec<_> = d.iter().map(|d| serde_json::json!({"name": d.name, "kind": format!("{:?}", d.kind).to_lowercase(), "handlers": d.handlers})).collect();
            println!("{}", serde_json::Value::Array(v));
        } else {
            for d in d {
                println!("{:<10} {}  ({})", format!("{:?}", d.kind).to_lowercase(), d.name, d.handlers);
            }
        }
        return Ok(ExitCode::SUCCESS);
    }
    let layers = Layers::load(&a.root)?;
    let merged = layers.effective();
    let sep = at(&merged, "keybindings.global.chord_sep").and_then(Value::as_str).unwrap_or(" ").to_owned();
    let mut draft = Draft::from_merged(&merged);
    let need = |n: usize| -> Result<(), String> {
        if a.pos.len() < n {
            Err(USAGE.into())
        } else {
            Ok(())
        }
    };
    match a.verb.as_deref() {
        None => {
            if !std::io::stdin().is_terminal() || !std::io::stdout().is_terminal() {
                return Err("the editor needs a terminal; use a verb (see --help) for headless use".into());
            }
            tui::run(layers)?;
            Ok(ExitCode::SUCCESS)
        }
        Some("list") => {
            if a.json {
                let v = serde_json::json!({
                    "user_tier": layers.user_path,
                    "bindings": draft.bindings,
                    "system_layer": draft.system_layer,
                });
                println!("{v}");
            } else {
                for b in &draft.bindings {
                    println!("{:<26} t{} {:<22} {}", field(b, "id"), b.get("tier").and_then(Value::as_integer).unwrap_or(0), field(b, "chord"), field(b, "action"));
                }
                for (k, v) in &draft.system_layer {
                    println!("{:<26} Guide+{k} {v}", "controller.system_layer");
                }
            }
            Ok(ExitCode::SUCCESS)
        }
        Some("check") => {
            let o = validate::Validator::from_ssot(&merged).run(&layers, &layers.user_text)?;
            println!("{}", o.summary);
            for f in &o.findings {
                println!("FINDING: {f}");
            }
            Ok(if o.findings.is_empty() { ExitCode::SUCCESS } else { ExitCode::from(1) })
        }
        Some("set") => {
            need(1)?;
            let i = draft.index(&a.pos[0]).ok_or_else(|| format!("no binding {}", a.pos[0]))?;
            edit_binding(&mut draft.bindings[i], &a, &sep)?;
            finish(&layers, &draft, a.apply)
        }
        Some("add") => {
            need(1)?;
            let id = &a.pos[0];
            if id.is_empty() || !id.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-') {
                return Err("id must be non-empty [a-z0-9-]".into());
            }
            if draft.index(id).is_some() {
                return Err(format!("binding {id} already exists; use set"));
            }
            if opt(&a, "chord").is_none() || opt(&a, "action").is_none() {
                return Err("add needs --chord and --action".into());
            }
            let mut b = toml::Table::new();
            b.insert("id".into(), Value::String(id.clone()));
            b.insert("tier".into(), Value::Integer(1));
            edit_binding(&mut b, &a, &sep)?;
            draft.bindings.push(b);
            finish(&layers, &draft, a.apply)
        }
        Some("remove") => {
            need(1)?;
            let i = draft.index(&a.pos[0]).ok_or_else(|| format!("no binding {}", a.pos[0]))?;
            draft.bindings.remove(i);
            finish(&layers, &draft, a.apply)
        }
        Some("pad") => {
            need(2)?;
            draft.system_layer.insert(a.pos[0].clone(), a.pos[1].clone());
            finish(&layers, &draft, a.apply)
        }
        Some("restore-defaults") => finish(&layers, &layers.defaults(), a.apply),
        Some("export") => {
            need(1)?;
            write_private_new(Path::new(&a.pos[0]), &model::keybindings_fragment(&draft)?)?;
            println!("exported {} binding(s) and {} system-layer button(s) to {}", draft.bindings.len(), draft.system_layer.len(), a.pos[0]);
            Ok(ExitCode::SUCCESS)
        }
        Some("import") => {
            need(1)?;
            let text = std::fs::read_to_string(&a.pos[0]).map_err(|e| format!("{}: {e}", a.pos[0]))?;
            let imported = model::import_fragment(&text, &draft)?;
            finish(&layers, &imported, a.apply)
        }
        Some(v) => Err(format!("unknown verb {v}\n{USAGE}")),
    }
}

fn main() -> ExitCode {
    let args = match parse_args(std::env::args().skip(1).collect()) {
        Ok(a) => a,
        Err(e) if e.is_empty() => {
            println!("{USAGE}");
            return ExitCode::SUCCESS;
        }
        Err(e) => {
            eprintln!("mios-controls: {e}");
            return ExitCode::from(64);
        }
    };
    match run(args) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("mios-controls: {e}");
            ExitCode::from(2)
        }
    }
}
