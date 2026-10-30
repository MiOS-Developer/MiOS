// AI-hint: Main entrypoint for mios-controls: with no verb (on a terminal) the binding-editor TUI; verbs list/check/set/add/remove/pad/room/rebind/restore-defaults/export/import drive the same draft -> validate -> apply pipeline headlessly (dry-run unless --apply), and uses/search/conflicts/layout/devices/profile read the binding map and draw the device views as text.
// AI-related: usr/share/mios/mios.toml [keybindings.global] [keybindings.tmux] [desktop.controls], tools/native/Cargo.toml, src/mios-rs/mios-gate/src/keybinds.rs, tests/test-mios-controls.py, tests/test-mios-controls-live.py

#![forbid(unsafe_code)]

mod apply;
mod bindmap;
mod devices;
mod input;
mod layout;
mod model;
mod pads;
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

/// [desktop.controls] string tunable.
pub fn controls_str(merged: &Value, key: &str, fallback: &str) -> String {
    at(merged, &format!("desktop.controls.{key}")).and_then(Value::as_str).filter(|s| !s.is_empty()).unwrap_or(fallback).to_owned()
}

/// [desktop.controls] integer tunable (absent or negative = automatic).
pub fn controls_int(merged: &Value, key: &str) -> Option<i64> {
    at(merged, &format!("desktop.controls.{key}")).and_then(Value::as_integer).filter(|v| *v >= 0)
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
  list [--json]             effective bindings, the Guide layer and the Hypr3D room table
  check                     validate the current map; exit 1 on findings
  uses INPUT [--json]       every action bound to an input: key:return, mouse:275, pad:BTN_SOUTH, or a chord
  search TEXT [--json]      every use whose key, button, chord, action or source matches
  conflicts [--json]        uses in conflict (local check + the gate's findings); exit 1 when there are any
  layout VIEW [--width W] [--height H] [--family F] [--layer L] [--glyphs auto|unicode|ascii] [--select INPUT]
                            draw keyboard | mouse | controller | bindings | devices as text
  devices [--json]          connected keyboards, mice and controllers (read-only, nothing is opened)
  profile DEVICE [--json]   one device (index or name part) and everything bound on it
  set ID [--chord C] [--action A] [--contexts a,b] [--passthrough a,b] [--bypass C] [--tier N] [--apply]
  add ID --chord C --action A [--tier N] [--contexts a,b] [--passthrough a,b] [--apply]
  remove ID [--apply]
  pad BUTTON ACTION [--apply]   Guide + BUTTON system-layer action
  room BUTTON ACTION [--apply]  Hypr3D room action (\"none\" unbinds; TAP,HOLD for a tap/hold pair)
  rebind REF --to INPUT [--layer L] [--apply]
                            move a use: REF = global:ID | bypass:ID | guide:BTN | room:BTN | tmux:KEY | table:PATH#N;
                            INPUT = key:x | mouse:276 | pad:BTN_WEST | a chord such as SUPER+SHIFT+x
  restore-defaults [--apply]    drop your binding overrides
  export FILE                   write the binding map alone to a new private file
  import FILE [--apply]         read a binding map exported by this tool
Every editing verb validates the draft and prints the user-tier binding paths it would write; --apply writes
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

/// Leaf rows of a controller table as ("<table>.<path>", value), tables first-level by name.
fn flatten(v: Option<&Value>, prefix: &str, out: &mut Vec<(String, String)>) {
    match v {
        Some(Value::Table(t)) => {
            for (k, v) in t {
                flatten(Some(v), &format!("{prefix}.{k}"), out);
            }
        }
        Some(Value::String(s)) => out.push((prefix.to_owned(), s.clone())),
        Some(v) => out.push((prefix.to_owned(), v.to_string())),
        None => {}
    }
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
    // Print only the editable binding paths: the user tier also holds credentials.
    let editable = layers.editable();
    let projected = model::parse_value(&text)?;
    let mut preview = toml::Table::new();
    for path in &editable {
        if let Some(v) = at(&projected, path) {
            let mut node = &mut preview;
            let segs: Vec<&str> = path.split('.').collect();
            for s in &segs[..segs.len() - 1] {
                node = node.entry(s.to_string()).or_insert_with(|| Value::Table(toml::Table::new())).as_table_mut().ok_or("preview")?;
            }
            node.insert(segs[segs.len() - 1].to_owned(), v.clone());
        }
    }
    if preview.is_empty() {
        println!("# user tier carries no binding overrides after this change ({})", layers.user_path.display());
    } else {
        println!("# user-tier binding paths after this change ({}):\n{}", layers.user_path.display(), toml::to_string(&preview).map_err(|e| e.to_string())?);
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

fn device_json(d: &devices::Device, families: &[devices::Family]) -> serde_json::Value {
    serde_json::json!({
        "name": d.name,
        "kind": d.kind.word(),
        "handlers": d.handlers,
        "event": d.event(),
        "vendor": format!("{:04x}", d.vendor),
        "product": format!("{:04x}", d.product),
        "family": if d.kind == devices::Kind::Controller { Some(d.family(families).id.clone()) } else { None },
        "virtual": d.is_virtual(),
        "fixture": d.is_fixture(),
    })
}

fn use_json(u: &bindmap::Use, flagged: bool) -> serde_json::Value {
    serde_json::json!({
        "input": u.input,
        "layer": u.layer,
        "chord": u.chord,
        "action": u.action,
        "label": u.label,
        "source": u.source.word(),
        "where": u.source.place(),
        "editable": u.editable(),
        "contexts": u.contexts,
        "note": u.note,
        "conflict": flagged,
    })
}

fn print_uses(uses: &[bindmap::Use], idx: &[usize], flagged: &std::collections::BTreeSet<usize>, json: bool) {
    if json {
        println!("{}", serde_json::Value::Array(idx.iter().map(|i| use_json(&uses[*i], flagged.contains(i))).collect()));
        return;
    }
    for i in idx {
        let u = &uses[*i];
        println!(
            "{}{:<8} {:<18} {:<24} {:<28} {}{}",
            if flagged.contains(i) { "!" } else if u.editable() { " " } else { "-" },
            u.source.word(),
            bindmap::layer_title(&u.layer),
            u.chord,
            u.action,
            u.label,
            if u.note.is_empty() { String::new() } else { format!(" ({})", u.note) }
        );
    }
}

/// The rendered compositor session for the current tier (best effort: the session's own binds are then listed too).
fn session_lua(layers: &Layers, merged: &Value) -> Option<String> {
    match validate::Validator::from_ssot(merged).render_lua(layers, &layers.user_text) {
        Ok(l) => Some(l),
        Err(e) => {
            eprintln!("mios-controls: the compositor session's own binds are not listed: {e}");
            None
        }
    }
}

fn run(a: Args) -> Result<ExitCode, String> {
    if a.verb.as_deref() == Some("devices") {
        let d = devices::scan();
        let fams = Layers::load(&a.root).map(|l| devices::Family::from_ssot(&l.effective())).unwrap_or_else(|_| vec![devices::Family::generic()]);
        if a.json {
            println!("{}", serde_json::Value::Array(d.iter().map(|d| device_json(d, &fams)).collect()));
        } else {
            for d in d {
                let fam = if d.kind == devices::Kind::Controller { format!(" [{}]", d.family(&fams).id) } else { String::new() };
                let flag = if d.is_virtual() { " (mios-inputd virtual)" } else if d.is_fixture() { " (fixture)" } else { "" };
                println!("{:<10} {}{fam}{flag}  ({}; {:04x}:{:04x})", d.kind.word(), d.name, d.handlers, d.vendor, d.product);
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
                    "editable": layers.editable(),
                    "bindings": draft.bindings,
                    "system_layer": draft.system_layer,
                    "room": draft.room,
                    "tmux": draft.tmux,
                    "tables": draft.tables,
                    "toggle_chord": model::toggle_chord(&merged),
                    "mode_override_chord": model::str_list(at(&merged, "keybindings.global.controller.mode_override_chord")),
                    "nav": at(&merged, "keybindings.global.controller.nav").cloned().unwrap_or(Value::Table(Default::default())),
                    "hypr3d": at(&merged, "desktop.hyprland.hypr3d.controller").cloned().unwrap_or(Value::Table(Default::default())),
                });
                println!("{v}");
            } else {
                for b in &draft.bindings {
                    println!("{:<26} t{} {:<22} {}", field(b, "id"), b.get("tier").and_then(Value::as_integer).unwrap_or(0), field(b, "chord"), field(b, "action"));
                }
                for (k, v) in &draft.system_layer {
                    println!("{:<26} Guide+{k} {v}", "controller.system_layer");
                }
                for (k, v) in &draft.room {
                    println!("{:<26} {k} {}", "controller.room", v.as_str().map(str::to_owned).unwrap_or_else(|| v.to_string()));
                }
                println!("{:<26} {} (tap: Hypr3D room, hold: release capture)", "controller.toggle_chord", model::toggle_chord(&merged).join("+"));
                println!("{:<26} {} (half-capture)", "controller.mode_override", model::str_list(at(&merged, "keybindings.global.controller.mode_override_chord")).join("+"));
                let mut rows = Vec::new();
                flatten(at(&merged, "keybindings.global.controller.nav"), "controller.nav", &mut rows);
                for (k, v) in rows {
                    println!("{k:<26} {v}");
                }
                if let Some(t) = at(&merged, "desktop.hyprland.hypr3d.controller").and_then(Value::as_table) {
                    for (k, v) in t.iter().filter(|(k, _)| k.ends_with("_button")) {
                        println!("{:<26} {k} {}", "controller.hypr3d", v.as_str().unwrap_or(""));
                    }
                }
            }
            Ok(ExitCode::SUCCESS)
        }
        Some("uses") | Some("search") | Some("conflicts") => {
            let lua = session_lua(&layers, &merged);
            let uses = bindmap::collect(&merged, &draft, lua.as_deref());
            let mut flagged = bindmap::conflicts(&uses);
            let verb = a.verb.as_deref().unwrap_or("");
            if verb == "conflicts" {
                let o = validate::Validator::from_ssot(&merged).run(&layers, &layers.user_text)?;
                let chords = bindmap::finding_chords(&o.findings);
                flagged.extend((0..uses.len()).filter(|i| chords.contains(&uses[*i].norm)));
            }
            let idx: Vec<usize> = match verb {
                "uses" => {
                    need(1)?;
                    let (input, layer) = tui::normalize_input(&a.pos[0])?;
                    (0..uses.len()).filter(|i| uses[*i].input == input && layer.as_deref().is_none_or(|l| uses[*i].layer == l)).collect()
                }
                "search" => {
                    need(1)?;
                    (0..uses.len()).filter(|i| uses[*i].matches(&a.pos.join(" "))).collect()
                }
                _ => flagged.iter().copied().collect(),
            };
            print_uses(&uses, &idx, &flagged, a.json);
            Ok(if verb == "conflicts" && !idx.is_empty() { ExitCode::from(1) } else { ExitCode::SUCCESS })
        }
        Some("layout") => {
            need(1)?;
            let num = |k: &str, d: u16| opt(&a, k).map(|v| v.parse::<u16>().map_err(|_| format!("--{k} must be a number"))).transpose().map(|v| v.unwrap_or(d));
            let (w, h) = (num("width", 170)?, num("height", 46)?);
            let lua = session_lua(&layers, &merged);
            print!("{}", tui::render_drawing(layers, &a.pos[0], w.clamp(20, 400), h.clamp(8, 200), opt(&a, "family"), opt(&a, "layer"), opt(&a, "glyphs"), lua, opt(&a, "select"))?);
            Ok(ExitCode::SUCCESS)
        }
        Some("profile") => {
            need(1)?;
            let devs = devices::scan();
            let fams = devices::Family::from_ssot(&merged);
            let q = a.pos.join(" ");
            let d = q.parse::<usize>().ok().and_then(|i| devs.get(i)).or_else(|| devs.iter().find(|d| d.name.to_ascii_lowercase().contains(&q.to_ascii_lowercase()))).ok_or_else(|| format!("no device {q:?} (see mios-controls devices)"))?;
            let kind = match d.kind {
                devices::Kind::Keyboard => "key",
                devices::Kind::Mouse => "mouse",
                devices::Kind::Controller => "pad",
            };
            let lua = session_lua(&layers, &merged);
            let uses = bindmap::collect(&merged, &draft, lua.as_deref());
            let flagged = bindmap::conflicts(&uses);
            let idx: Vec<usize> = (0..uses.len()).filter(|i| uses[*i].kind() == kind).collect();
            if a.json {
                println!("{}", serde_json::json!({"device": device_json(d, &fams), "uses": idx.iter().map(|i| use_json(&uses[*i], flagged.contains(i))).collect::<Vec<_>>()}));
            } else {
                println!("{} ({}, {:04x}:{:04x}, {})", d.name, d.kind.word(), d.vendor, d.product, d.event().unwrap_or("-"));
                if d.kind == devices::Kind::Controller {
                    let f = d.family(&fams);
                    println!("family {} ({} layout)", f.title, f.layout);
                }
                print_uses(&uses, &idx, &flagged, false);
            }
            Ok(ExitCode::SUCCESS)
        }
        Some("rebind") => {
            need(1)?;
            let to = opt(&a, "to").ok_or("rebind needs --to INPUT")?.to_owned();
            let reference = a.pos[0].clone();
            let lay = Layers::load(&a.root)?;
            let (new, what) = tui::cli_rebind(lay, &reference, &to, opt(&a, "layer"))?;
            println!("{what}");
            finish(&layers, &new, a.apply)
        }
        Some("room") => {
            need(2)?;
            let v = if a.pos[1].contains(',') { list_value(&a.pos[1]) } else { Value::String(a.pos[1].clone()) };
            draft.room.insert(a.pos[0].clone(), v);
            finish(&layers, &draft, a.apply)
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
            write_private_new(Path::new(&a.pos[0]), &model::keybindings_fragment(&draft, &layers.editable())?)?;
            println!("exported {} binding(s), {} Guide-layer and {} room button(s), {} tmux-os key(s) to {}", draft.bindings.len(), draft.system_layer.len(), draft.room.len(), draft.tmux.len(), a.pos[0]);
            Ok(ExitCode::SUCCESS)
        }
        Some("import") => {
            need(1)?;
            let text = std::fs::read_to_string(&a.pos[0]).map_err(|e| format!("{}: {e}", a.pos[0]))?;
            let imported = model::import_fragment(&text, &draft, &layers.editable())?;
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
