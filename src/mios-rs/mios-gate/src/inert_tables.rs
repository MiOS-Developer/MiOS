// AI-hint: Asserts every top-level mios.toml table has an access-shaped consumer or sits in the shrink-only [ssot_tables] register; subscript evidence requires the subscripted value to BE the parsed SSOT.
// AI-related: usr/share/mios/mios.toml, automation/98-drift-checks.sh, tests/drift-gate-negatives.sh

use crate::Report;
use regex::Regex;
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;
use std::process::Command;

const CHECK: &str = "no-inert-ssot-tables";
const SSOT: &str = "usr/share/mios/mios.toml";
const CODE_EXT: [&str; 7] = [".py", ".sh", ".ps1", ".rs", ".js", ".ts", ".mjs"];
const NON_CONSUMER_DIRS: [&str; 3] = ["docs/", "usr/share/doc/", "usr/share/mios/reference/"];
/// concat! so the contiguous marker never appears here (task_945).
const GENERATED: &str = concat!("# GENERATED IN FULL", " from usr/share/mios/mios.toml");

fn report(ok: bool, summary: String, findings: Vec<String>) -> Report {
    Report {
        check: CHECK.to_string(),
        ok,
        could_not_run: None,
        summary,
        findings,
    }
}

fn cannot_run(why: impl Into<String>) -> Report {
    Report {
        check: CHECK.to_string(),
        ok: false,
        could_not_run: Some(why.into()),
        summary: String::new(),
        findings: Vec::new(),
    }
}

/// Every `MIOS_<TABLE>_<TOKEN>` token this table's own key names could project.
fn key_tokens(v: &toml::Value, out: &mut BTreeSet<String>) {
    if let Some(t) = v.as_table() {
        for (k, sub) in t {
            for tok in k.to_uppercase().replace('-', "_").split('_') {
                if !tok.is_empty() {
                    out.insert(tok.to_string());
                }
            }
            key_tokens(sub, out);
        }
    }
}

/// Names holding the parsed SSOT, closed transitively (task_945).
fn ssot_names(text: &str, re: &Res) -> BTreeSet<String> {
    let loaders: Vec<String> = re
        .def
        .captures_iter(text)
        .filter(|c| {
            let end = c.get(0).map(|m| m.end()).unwrap_or(0);
            // A byte window, so its far edge can split a multi-byte char.
            let tail = &text[end..text.floor_char_boundary(end + 2000)];
            re.load.is_match(tail)
        })
        .filter_map(|c| c.get(1).map(|m| m.as_str().to_string()))
        .collect();

    // Scan bindings ONCE: the fixpoint must not re-scan (quadratic).
    let mut binds: Vec<(String, String)> = Vec::new();
    for re_bind in [&re.bind, &re.bindkw, &re.forin] {
        for c in re_bind.captures_iter(text) {
            if let (Some(n), Some(e)) = (c.get(1), c.get(2)) {
                binds.push((n.as_str().to_string(), e.as_str().to_string()));
            }
        }
    }
    if binds.is_empty() {
        return BTreeSet::new();
    }

    let mut names: BTreeSet<String> = binds
        .iter()
        .filter(|(_, e)| {
            re.load.is_match(e)
                || loaders.iter().any(|l| {
                    e.find(l.as_str())
                        .map(|i| e[i + l.len()..].trim_start().starts_with('('))
                        .unwrap_or(false)
                })
        })
        .map(|(n, _)| n.clone())
        .collect();

    loop {
        let grew: Vec<String> = binds
            .iter()
            .filter(|(n, e)| !names.contains(n) && names.iter().any(|k| word_in(e, k)))
            .map(|(n, _)| n.clone())
            .collect();
        if grew.is_empty() {
            break;
        }
        names.extend(grew);
    }
    names
}

/// `needle` appearing in `hay` on identifier boundaries.
fn word_in(hay: &str, needle: &str) -> bool {
    let bytes = hay.as_bytes();
    let mut from = 0;
    while let Some(i) = hay[from..].find(needle) {
        let s = from + i;
        let e = s + needle.len();
        let before_ok = s == 0 || !(bytes[s - 1].is_ascii_alphanumeric() || bytes[s - 1] == b'_');
        let after_ok = e >= bytes.len() || !(bytes[e].is_ascii_alphanumeric() || bytes[e] == b'_');
        if before_ok && after_ok {
            return true;
        }
        from = e;
    }
    false
}

/// Table names this file reads OUT OF the SSOT: a subscript counts only when what
/// it indexes IS the parse, or a name bound from one (task_945).
fn ssot_keys(text: &str, re: &Res) -> BTreeSet<String> {
    let names = ssot_names(text, re);
    let mut keys = BTreeSet::new();
    for c in re.sub.captures_iter(text) {
        let holder = c.get(1).map(|m| m.as_str()).unwrap_or("");
        if names.contains(holder) {
            if let Some(k) = c.get(2).or_else(|| c.get(3)) {
                keys.insert(k.as_str().to_string());
            }
        }
    }
    for c in re.value.captures_iter(text) {
        if names.contains(c.get(1).map(|m| m.as_str()).unwrap_or("")) {
            if let Some(key) = c.get(2) {
                keys.insert(key.as_str().to_string());
            }
        }
    }
    for line in text.lines() {
        if re.load.is_match(line) {
            for c in re.sub.captures_iter(line) {
                if let Some(k) = c.get(2).or_else(|| c.get(3)) {
                    keys.insert(k.as_str().to_string());
                }
            }
        }
    }
    keys
}

/// Table -> (context-free evidence patterns, optional MIOS_<TABLE>_* projection rule).
type Pending = BTreeMap<String, (Vec<Regex>, Option<(Regex, BTreeSet<String>)>)>;

struct Res {
    load: Regex,
    bind: Regex,
    bindkw: Regex,
    forin: Regex,
    def: Regex,
    sub: Regex,
    value: Regex,
    ctx: Regex,
    test_path: Regex,
}

fn build_res() -> Option<Res> {
    // .ok()? not expect(): CI runs clippy -D warnings.
    Some(Res {
        // Seeds for "yields the SSOT"; full path only (task_945). resolve_merged is
        // the native resolver's twin of load_merged.
        load: Regex::new(
            r"(?:tomllib|toml|rtoml|tomlkit)\.loads?\(|toml::from_str|(?:load|resolve)_merged|_toml_section|mios_toml\.(?:load|resolve)|load_mios_toml|MIOS_TOML|usr/share/mios/mios\.toml|\b_?ssot(?:_root)?\s*\(|\bload_ssot\s*\(",
        )
        .ok()?,
        bind: Regex::new(r"(?m)^[^\S\n]*([A-Za-z_]\w*)[^\S\n]*(?::[^=\n]+)?=[^\S\n]*(.*)$").ok()?,
        // Rust/TS binding forms; the bare form above cannot see them.
        bindkw: Regex::new(
            r"(?m)^[^\S\n]*(?:let|const|static|var)[^\S\n]+(?:mut[^\S\n]+)?([A-Za-z_]\w*)[^\S\n]*(?::[^=\n]+)?=[^\S\n]*(.*)$",
        )
        .ok()?,
        forin: Regex::new(r"(?m)^[^\S\n]*for[^\S\n]+([A-Za-z_]\w*)[^\S\n]+in[^\S\n]+(.*)$").ok()?,
        def: Regex::new(r"(?m)^[^\S\n]*def[^\S\n]+([A-Za-z_]\w*)[^\S\n]*\(").ok()?,
        sub: Regex::new(r#"([A-Za-z_]\w*)?\s*(?:\[\s*["'](\w+)["']\s*\]|\.get\(\s*["'](\w+)["'])"#)
            .ok()?,
        value: Regex::new(r#"\bconfig_value\(\s*&?([A-Za-z_]\w*)\s*,\s*"root"\s*,\s*"(\w+)"\s*\)"#)
            .ok()?,
        ctx: Regex::new(r"mios\.toml|mios_toml|_toml_section|(?:load|resolve)_merged|MIOS_TOML")
            .ok()?,
        test_path: Regex::new(r"(^|/)tests?/|(^|/)test[-_]").ok()?,
    })
}

/// The non-subscript evidence shapes, which do not need SSOT context because each
/// names the table in a position only an SSOT read produces.
fn anywhere_patterns(table: &str, keys: &[String]) -> Vec<Regex> {
    let t = regex::escape(table);
    let mut out = Vec::new();
    if !keys.is_empty() {
        let alts: Vec<String> = keys.iter().map(|k| regex::escape(k)).collect();
        // a quoted dotted path is evidence only when it names a REAL key of the
        // table, so "git.exe" can never stand in for [git]
        if let Ok(r) = Regex::new(&format!(r#"["']{t}\.(?:{})\b"#, alts.join("|"))) {
            out.push(r);
        }
    }
    for p in [
        format!(r#"_toml_section\(\s*["']{t}["']"#),
        format!(r#"toml[-_]get\s+["']?{t}[.\s"']"#),
        format!(r#"toml[-_]value\S*\s+["']?{t}["'.\s]"#),
        format!(r#"-Section\s+["']{t}(?:\.[a-z0-9_]+)?["']"#),
        format!(r#"startswith\(\s*["']{t}\."#),
        format!(r"\\\[{t}\\\]"),
    ] {
        if let Ok(r) = Regex::new(&p) {
            out.push(r);
        }
    }
    out
}

pub fn check(root: &Path) -> Report {
    let ssot = root.join(SSOT);
    let raw = match std::fs::read_to_string(&ssot) {
        Ok(s) => s,
        Err(e) => return cannot_run(format!("{SSOT} unreadable: {e}")),
    };
    let data: toml::Value = match raw.parse() {
        Ok(v) => v,
        Err(e) => return cannot_run(format!("{SSOT} does not parse: {e}")),
    };
    let top = match data.as_table() {
        Some(t) => t,
        None => return cannot_run(format!("{SSOT} is not a table")),
    };

    let out = Command::new("git")
        .arg("-C")
        .arg(root)
        .args(["ls-files"])
        .output();
    let listing = match out {
        Ok(o) if o.status.success() => String::from_utf8_lossy(&o.stdout).into_owned(),
        _ => return cannot_run("git ls-files failed -- the consumer corpus is unknown"),
    };

    let Some(re) = build_res() else {
        return cannot_run("a built-in pattern failed to compile");
    };
    let tables: BTreeSet<String> = top
        .iter()
        .filter(|(_, v)| v.is_table())
        .map(|(k, _)| k.clone())
        .collect();

    let mut pending: Pending = BTreeMap::new();
    for name in &tables {
        let val = &top[name];
        let keys: Vec<String> = val
            .as_table()
            .map(|t| t.keys().cloned().collect())
            .unwrap_or_default();
        let mut toks = BTreeSet::new();
        key_tokens(val, &mut toks);
        let var = if toks.is_empty() {
            None
        } else {
            Regex::new(&format!(
                r"MIOS_{}_([A-Z0-9_]+)",
                name.to_uppercase().replace('-', "_")
            ))
            .ok()
            .map(|r| (r, toks))
        };
        pending.insert(name.clone(), (anywhere_patterns(name, &keys), var));
    }

    let mut consumed: BTreeSet<String> = BTreeSet::new();
    // [dotfiles.registry.*].section is the SSOT's own consumer manifest: the
    // dotfiles renderer resolves each registered template from that table.
    if let Some(reg) = data
        .get("dotfiles")
        .and_then(|d| d.get("registry"))
        .and_then(|r| r.as_table())
    {
        for entry in reg.values() {
            if let Some(sec) = entry.get("section").and_then(|s| s.as_str()) {
                if tables.contains(sec) {
                    consumed.insert(sec.to_string());
                    pending.remove(sec);
                }
            }
        }
    }

    for rel in listing.lines() {
        if pending.is_empty() {
            break;
        }
        if rel == SSOT || re.test_path.is_match(rel) {
            continue;
        }
        if NON_CONSUMER_DIRS.iter().any(|d| rel.starts_with(d)) || rel.contains("/docs/") {
            continue;
        }
        let path = root.join(rel);
        let bytes = match std::fs::read(&path) {
            Ok(b) => b,
            Err(_) => continue,
        };
        let is_code = CODE_EXT.iter().any(|e| rel.ends_with(e)) || bytes.starts_with(b"#!");
        if !is_code {
            continue;
        }
        let head_len = bytes.len().min(4096);
        let text = String::from_utf8_lossy(&bytes);
        if text[..text.floor_char_boundary(head_len)].contains(GENERATED) {
            continue;
        }
        let ssot_file = re.ctx.is_match(&text);
        let keys = if ssot_file {
            ssot_keys(&text, &re)
        } else {
            BTreeSet::new()
        };

        let hits: Vec<String> = pending
            .iter()
            .filter(|(name, (anywhere, var))| {
                anywhere.iter().any(|p| p.is_match(&text))
                    || keys.contains(name.as_str())
                    || var
                        .as_ref()
                        .map(|(r, toks)| {
                            r.captures_iter(&text).any(|c| {
                                c.get(1)
                                    .map(|m| m.as_str().split('_').any(|t| toks.contains(t)))
                                    .unwrap_or(false)
                            })
                        })
                        .unwrap_or(false)
            })
            .map(|(n, _)| n.clone())
            .collect();
        for h in hits {
            consumed.insert(h.clone());
            pending.remove(&h);
        }
    }

    let mut viol: Vec<String> = Vec::new();
    let reg_tbl = data.get("ssot_tables").and_then(|v| v.as_table());
    let (register, ceiling) = match reg_tbl {
        None => {
            viol.push(
                "[ssot_tables] is absent -- nothing bounds how many SSOT tables may sit dead \
                 with no consumer"
                    .into(),
            );
            (Vec::new(), None)
        }
        Some(t) => {
            let reg: Option<Vec<String>> =
                t.get("unconsumed").and_then(|v| v.as_array()).map(|a| {
                    a.iter()
                        .filter_map(|x| x.as_str().map(|s| s.to_string()))
                        .collect()
                });
            let ceiling = t.get("max_unconsumed").and_then(|v| v.as_integer());
            if reg.is_none() {
                viol.push(
                    "[ssot_tables] declares no `unconsumed` key -- an implied empty register is \
                     indistinguishable from a forgotten one"
                        .into(),
                );
            }
            if ceiling.is_none() {
                viol.push(
                    "[ssot_tables].max_unconsumed is unset -- without a ceiling the register \
                     absorbs new breakage as fast as it appears"
                        .into(),
                );
            }
            (reg.unwrap_or_default(), ceiling)
        }
    };

    let uniq: BTreeSet<&String> = register.iter().collect();
    if uniq.len() != register.len() {
        let mut dupes: Vec<&String> = register
            .iter()
            .filter(|r| register.iter().filter(|x| x == r).count() > 1)
            .collect();
        dupes.sort();
        dupes.dedup();
        viol.push(format!(
            "[ssot_tables].unconsumed lists a table twice: {}",
            dupes
                .iter()
                .map(|s| s.as_str())
                .collect::<Vec<_>>()
                .join(", ")
        ));
    }
    let mut sorted = register.clone();
    sorted.sort();
    if sorted != register {
        viol.push(
            "[ssot_tables].unconsumed is not sorted -- an unsorted register hides an addition \
             inside a reordering"
                .into(),
        );
    }
    for name in &register {
        if !tables.contains(name) {
            viol.push(format!(
                "[ssot_tables].unconsumed entry `{name}` names a table the SSOT no longer \
                 declares -- drop the entry"
            ));
        } else if consumed.contains(name) {
            viol.push(format!(
                "[ssot_tables].unconsumed entry `{name}` has a consumer now -- drop it from the \
                 register; the register only shrinks"
            ));
        }
    }
    if let Some(c) = ceiling {
        let n = register.len() as i64;
        if n > c {
            viol.push(format!(
                "[ssot_tables].unconsumed holds {n} entries, over the ratchet ceiling \
                 max_unconsumed = {c}. The ceiling only comes DOWN"
            ));
        } else if n < c {
            viol.push(format!(
                "[ssot_tables].max_unconsumed = {c} exceeds the {n} registered entries -- lower \
                 it to {n} so the ground gained is held"
            ));
        }
    }

    let registered: BTreeSet<&String> = register.iter().collect();
    for name in &tables {
        if !consumed.contains(name) && !registered.contains(name) {
            viol.push(format!(
                "SSOT table [{name}] has no access-shaped consumer -- wire it, drop it, or \
                 record it in [ssot_tables].unconsumed with a reason"
            ));
        }
    }
    if consumed.is_empty() {
        viol.push(
            "no consumption evidence found for ANY table -- the gate would pass vacuously over \
             an empty set"
                .into(),
        );
    }

    let summary = format!(
        "tables={} consumed={} registered-unconsumed={} (ceiling {})",
        tables.len(),
        consumed.len(),
        register.len(),
        ceiling
            .map(|c| c.to_string())
            .unwrap_or_else(|| "unset".into())
    );
    report(viol.is_empty(), summary, viol)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn keys(src: &str) -> BTreeSet<String> {
        ssot_keys(src, &build_res().unwrap_or_else(|| unreachable!()))
    }

    /// THE DEFECT (task_945): a local dict must not credit a table.
    #[test]
    fn local_dict_subscript_is_not_ssot_evidence() {
        let src = "# reads usr/share/mios/mios.toml elsewhere\np = _offline_posture()\nif p[\"offline\"]:\n    pass\n";
        assert!(
            !keys(src).contains("offline"),
            "a subscript of an unrelated dict must not credit a table"
        );
    }

    /// The other side: a real read must still credit.
    #[test]
    fn a_subscript_on_the_parse_itself_is_evidence() {
        let src = "ceiling = tomllib.load(fh)[\"tests\"][\"max_checks_without_negative\"]\n";
        assert!(
            keys(src).contains("tests"),
            "a read off the parse must credit"
        );
    }

    #[test]
    fn a_name_bound_from_the_parse_is_evidence() {
        let src = "with open(p, \"rb\") as f:\n    data = tomllib.load(f)\ncfg = data.get(\"cockpit\", {})\n";
        assert!(
            keys(src).contains("cockpit"),
            "a name bound from the parse must credit"
        );
    }

    #[test]
    fn native_root_accessor_on_parsed_ssot_is_evidence() {
        let src = "let doc: toml::Value = toml::from_str(ssot)?;\nlet config = config_value(&doc, \"root\", \"cockpit\")?;\n";
        assert!(keys(src).contains("cockpit"));
    }

    #[test]
    fn native_accessor_on_unrelated_or_nested_value_is_not_root_evidence() {
        let unrelated = "let doc = unrelated_config();\nlet config = config_value(&doc, \"root\", \"cockpit\")?;\n";
        let nested = "let doc: toml::Value = toml::from_str(ssot)?;\nlet config = config_value(&doc, \"identity\", \"cockpit\")?;\n";
        assert!(!keys(unrelated).contains("cockpit"));
        assert!(!keys(nested).contains("cockpit"));
    }

    /// The native layered resolver yields the SSOT exactly as load_merged does;
    /// generators ported to Rust read their tables off it.
    #[test]
    fn native_resolve_merged_binding_is_evidence() {
        let src = "let doc = mios_resolver::resolve_merged(Some(root), false)?;\nlet networks = doc\n    .get(\"networks\")\n    .and_then(|v| v.as_table());\n";
        let re = build_res().unwrap_or_else(|| unreachable!());
        assert!(
            re.ctx.is_match(src),
            "the file must be scanned as an SSOT reader"
        );
        assert!(keys(src).contains("networks"));
        let unrelated =
            "let doc = other::resolve(Some(root))?;\nlet networks = doc\n    .get(\"networks\");\n";
        assert!(!re.ctx.is_match(unrelated));
        assert!(!keys(unrelated).contains("networks"));
    }

    /// One indirection through a loader helper must still credit.
    #[test]
    fn a_helper_that_parses_makes_its_callers_binding_evidence() {
        let src = "def _ssot_root():\n    return tomllib.load(open(P, 'rb'))\n_SSOT = _ssot_root()\n_DB = _SSOT.get(\"database\") or {}\n";
        assert!(
            keys(src).contains("database"),
            "one indirection must still credit"
        );
    }

    /// REGRESSION: the binder must not cross newlines (task_945).
    #[test]
    fn the_binder_does_not_cross_newlines() {
        let src = "try:\n    with open(p, 'rb') as f:\n        data = tomllib.load(f)\n    x = data[\"kargs\"]\n";
        let names = ssot_names(src, &build_res().unwrap_or_else(|| unreachable!()));
        assert!(
            names.contains("data"),
            "the real binding must survive: {names:?}"
        );
        assert!(!names.contains("try"), "`try` is not a binding: {names:?}");
    }

    /// REGRESSION: the 2000-byte loader window ended inside a multi-byte char
    /// in mios.toml-reading code and panicked the whole gate.
    #[test]
    fn the_loader_window_never_splits_a_multibyte_char() {
        let head = "def f():\n    return tomllib.load(fh)\n";
        let pad = "#".repeat(2005 - head.len());
        let src = format!("{head}{pad}\u{270e}\n_S = f()\nv = _S[\"tbl\"]\n");
        assert!(
            !src.is_char_boundary("def f(".len() + 2000),
            "the fixture must put the window edge inside the char"
        );
        assert!(keys(&src).contains("tbl"), "the loader must still credit");
    }

    #[test]
    fn word_boundaries_are_respected() {
        assert!(word_in("a = cfg.get('x')", "cfg"));
        assert!(!word_in("a = cfgx.get('x')", "cfg"));
        assert!(!word_in("a = mycfg", "cfg"));
    }
}
