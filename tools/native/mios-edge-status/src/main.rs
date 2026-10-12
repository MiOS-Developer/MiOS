// AI-hint: Edge-to-edge reach gate: prints one line per mios.toml [theme.edge.reach] key, measured from its committed artifact against [theme].padding/scrollbar_state and [theme.edge]; exit 1 on DRIFT, 2 on unclassified.
// AI-related: usr/share/mios/mios.toml, usr/share/mios/theme/fixtures/edge/padding-cases.tsv, usr/lib/mios/mios_toml.py, usr/libexec/mios/mios-vscode-custom-css
// AI-functions: parse_padding, load_vendor, run, main

#![forbid(unsafe_code)]
#![warn(clippy::unwrap_used, clippy::expect_used, clippy::panic)]

use regex::Regex;
use serde_json::Value as Json;
use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::sync::OnceLock;
use toml::{Table, Value};

const SSOT: &str = "usr/share/mios/mios.toml";
const VENDOR_D: &str = "usr/lib/mios/mios.d";
const TTYD_LAUNCHER: &str = "usr/libexec/mios/mios-ttyd-launch";
const CS_WORKBENCH: &str = "lib/vscode/out/vs/code/browser/workbench/workbench.js";
const REACHES: [&str; 4] = ["full", "partial", "none", "n/a"];
const SCROLLBAR_STATES: [&str; 3] = ["visible", "hidden", "always"]; // WT profiles.schema.json scrollbarState enum
const DENSITY_KEY: &str = "window.density.layout";
const MODERN_UI_KEY: &str = "workbench.experimental.modernUI";
const PORTAL_EDGE: &str = "usr/lib/mios/agent-pipe/mios_pipe/routing/portal_edge.py";
const USAGE: &str = "usage: mios-edge-status [--root DIR] [--bootstrap DIR] [--code-server-root DIR]\n  exit 0 nothing drifted, 1 DRIFT, 2 unclassified, 3 usage error or unreadable SSOT\n";

/// Terminal insets in WT order: left, top, right, bottom.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Insets {
    pub left: u64,
    pub top: u64,
    pub right: u64,
    pub bottom: u64,
}

fn re(cell: &'static OnceLock<Regex>, pat: &str) -> &'static Regex {
    cell.get_or_init(|| Regex::new(pat).unwrap_or_else(|e| unreachable!("static regex {pat}: {e}")))
}

/// The WT padding grammar, integer non-negative subset; parity with mios_toml.edge_insets() via padding-cases.tsv.
pub fn parse_padding(raw: &str) -> Option<Insets> {
    static R: OnceLock<Regex> = OnceLock::new();
    let c = re(
        &R,
        r"\A([0-9]+)(?: *, *([0-9]+)(?: *, *([0-9]+) *, *([0-9]+))?)?\z",
    )
    .captures(raw)?;
    let mut n = Vec::new();
    for i in 1..=4 {
        if let Some(m) = c.get(i) {
            n.push(m.as_str().parse::<u64>().ok()?);
        }
    }
    match n.as_slice() {
        [a] => Some(Insets {
            left: *a,
            top: *a,
            right: *a,
            bottom: *a,
        }),
        [a, b] => Some(Insets {
            left: *a,
            top: *b,
            right: *a,
            bottom: *b,
        }),
        [a, b, c, d] => Some(Insets {
            left: *a,
            top: *b,
            right: *c,
            bottom: *d,
        }),
        _ => None,
    }
}

fn deep_merge(dst: &mut Table, src: Table) {
    for (k, v) in src {
        match (dst.get_mut(&k), v) {
            (Some(Value::Table(d)), Value::Table(s)) => deep_merge(d, s),
            (Some(Value::String(d)), Value::String(s)) if s.is_empty() && !d.is_empty() => {}
            (_, v) => {
                dst.insert(k, v);
            }
        }
    }
}

/// The vendor tier only (monolith + mios.d fragments, Law 13), because every measured artifact is a committed vendor render.
pub fn load_vendor(root: &Path) -> Result<Table, String> {
    let mono = std::env::var_os("MIOS_VENDOR_TOML")
        .map(PathBuf::from)
        .unwrap_or_else(|| root.join(SSOT));
    let dir = std::env::var_os("MIOS_VENDOR_TOML_D")
        .map(PathBuf::from)
        .unwrap_or_else(|| root.join(VENDOR_D));
    let read = |p: &Path| -> Result<Table, String> {
        let text = fs::read_to_string(p).map_err(|e| format!("{}: {e}", p.display()))?;
        text.parse::<Table>()
            .map_err(|e| format!("{}: {e}", p.display()))
    };
    let mut merged = read(&mono)?;
    if let Ok(rd) = fs::read_dir(&dir) {
        let mut frags: Vec<PathBuf> = rd
            .filter_map(|e| e.ok().map(|e| e.path()))
            .filter(|p| p.extension().is_some_and(|x| x == "toml"))
            .collect();
        frags.sort_by_key(|p| p.file_name().map(|n| n.to_os_string()));
        for f in frags {
            deep_merge(&mut merged, read(&f)?);
        }
    }
    Ok(merged)
}

fn get<'a>(t: &'a Table, dotted: &str) -> Option<&'a Value> {
    let mut parts = dotted.split('.');
    let mut cur = t.get(parts.next()?)?;
    for p in parts {
        cur = cur.as_table()?.get(p)?;
    }
    Some(cur)
}

fn get_str<'a>(t: &'a Table, dotted: &str) -> Option<&'a str> {
    get(t, dotted).and_then(Value::as_str)
}

/// A CSS length in whole px ("0", "Npx", "calc(-1 * Npx)" as N) with !important stripped.
fn css_px(v: &str) -> Option<i64> {
    static CALC: OnceLock<Regex> = OnceLock::new();
    let v = v.replace("!important", "");
    let v = v.trim();
    if let Some(c) = re(&CALC, r"\Acalc\(\s*-1\s*\*\s*(\d+)px\s*\)\z").captures(v) {
        return c[1].parse().ok();
    }
    v.strip_suffix("px")
        .unwrap_or(v)
        .parse::<i64>()
        .ok()
        .filter(|n| v.ends_with("px") || *n == 0)
}

/// (selector, [(property, value)]) for every flat CSS rule, comments stripped.
fn css_rules(css: &str) -> Vec<(String, Vec<(String, String)>)> {
    static COMMENT: OnceLock<Regex> = OnceLock::new();
    static RULE: OnceLock<Regex> = OnceLock::new();
    let css = re(&COMMENT, r"(?s)/\*.*?\*/").replace_all(css, "");
    re(&RULE, r"([^{}]+)\{([^{}]*)\}")
        .captures_iter(&css)
        .map(|c| {
            let decls = c[2]
                .split(';')
                .filter_map(|d| d.split_once(':'))
                .map(|(p, v)| {
                    (
                        p.trim().to_ascii_lowercase(),
                        v.replace("!important", "").trim().to_string(),
                    )
                })
                .collect();
            (c[1].split_whitespace().collect::<Vec<_>>().join(" "), decls)
        })
        .collect()
}

fn selectors(sel: &str) -> impl Iterator<Item = &str> {
    sel.split(',').map(str::trim)
}

struct Cx {
    root: PathBuf,
    bootstrap: Option<PathBuf>,
    cs_root: Option<PathBuf>,
    data: Table,
    insets: Insets,
    padding: String,
    scrollbar: String,
}

impl Cx {
    fn int(&self, dotted: &str) -> Result<i64, String> {
        get(&self.data, dotted)
            .and_then(Value::as_integer)
            .ok_or_else(|| format!("[{dotted}] is not an integer"))
    }
    fn hidden(&self) -> bool {
        self.scrollbar == SCROLLBAR_STATES[1]
    }
}

#[derive(Default)]
struct Probe {
    facts: Vec<String>,
    drifts: Vec<(String, String, String)>,
}

impl Probe {
    fn fact(&mut self, f: impl Into<String>) {
        self.facts.push(f.into());
    }
    fn drift(&mut self, prop: impl Into<String>, got: impl Into<String>, want: impl Into<String>) {
        self.drifts.push((prop.into(), got.into(), want.into()));
    }
    fn check_int(&mut self, prop: &str, got: Option<i64>, want: Result<i64, String>) {
        match (got, want) {
            (_, Err(e)) => self.drift(prop, "?", e),
            (None, Ok(w)) => self.drift(prop, "missing", w.to_string()),
            (Some(g), Ok(w)) if g != w => self.drift(prop, g.to_string(), w.to_string()),
            (Some(g), Ok(_)) => self.fact(format!("{prop}={g}")),
        }
    }
}

type Measure = fn(&Cx, &Path, &mut Probe);

fn measurer(artifact: &str) -> Option<Measure> {
    let name = artifact.rsplit('/').next().unwrap_or(artifact);
    Some(match name {
        n if n.ends_with(".ps1") => m_wt_ps1,
        "settings.json" => m_vscode_settings,
        "ttyd-page.json" => m_ttyd,
        n if n.ends_with(".json") => m_wt_json,
        "code-server-terminal.css" => m_code_server,
        "mios-edge.css" => m_gtk4,
        "portal-term.css" => m_portal,
        "config.jsonc" => m_fastfetch,
        "hyprland.conf" => m_wm,
        "alacritty.toml" => m_alacritty,
        _ => return None,
    })
}

fn read(path: &Path, p: &mut Probe) -> Option<String> {
    match fs::read_to_string(path) {
        Ok(t) => Some(t),
        Err(_) => {
            p.drift("artifact", "missing", path.display().to_string());
            None
        }
    }
}

fn walk_json<'a>(v: &'a Json, key: &str, out: &mut Vec<&'a str>) {
    match v {
        Json::Object(m) => {
            for (k, x) in m {
                match x.as_str() {
                    Some(s) if k == key => out.push(s),
                    _ => walk_json(x, key, out),
                }
            }
        }
        Json::Array(a) => a.iter().for_each(|x| walk_json(x, key, out)),
        _ => {}
    }
}

fn m_wt_json(cx: &Cx, path: &Path, p: &mut Probe) {
    let Some(text) = read(path, p) else { return };
    let Ok(doc) = serde_json::from_str::<Json>(&text) else {
        return p.drift("json", "unparseable", "valid JSON");
    };
    for (key, want) in [("padding", &cx.padding), ("scrollbarState", &cx.scrollbar)] {
        let mut found = Vec::new();
        walk_json(&doc, key, &mut found);
        if found.is_empty() {
            p.drift(key, "missing", want.as_str());
        }
        let mut bad: Vec<&str> = found
            .iter()
            .copied()
            .filter(|v| {
                if key == "padding" {
                    parse_padding(v) != Some(cx.insets)
                } else {
                    v != want
                }
            })
            .collect();
        bad.dedup();
        for v in bad {
            p.drift(key, v, want.as_str());
        }
        if let Some(v) = found.first() {
            p.fact(format!("{key}[{}]={v}", found.len()));
        }
    }
}

fn m_wt_ps1(cx: &Cx, path: &Path, p: &mut Probe) {
    let Some(text) = read(path, p) else { return };
    let name = path
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default();
    let tag = match &cx.bootstrap {
        Some(b) if path.starts_with(b) => format!("bootstrap:{name}"),
        _ => name,
    };
    for (wt, ssot_key, want) in [
        ("padding", "padding", &cx.padding),
        ("scrollbarState", "scrollbar_state", &cx.scrollbar),
    ] {
        let read_re = format!(
            r"\$(\w+)\s*=\s*Get-MiosTomlValue\s+-Section\s+'theme'\s+-Key\s+'{ssot_key}'\s+-Default\s+'([^']*)'"
        );
        let Some(c) = Regex::new(&read_re).ok().and_then(|r| r.captures(&text)) else {
            p.drift(
                format!("{tag}:{wt}_read"),
                "missing",
                format!("[theme].{ssot_key}"),
            );
            continue;
        };
        let var = regex::escape(&c[1]);
        let mut defaults = vec![c[2].to_string()];
        if let Ok(r) = Regex::new(&format!(r"\${var}\s*=\s*'([^']*)'")) {
            defaults.extend(r.captures_iter(&text).map(|d| d[1].to_string()));
        }
        defaults.dedup();
        for d in &defaults {
            let ok = if wt == "padding" {
                parse_padding(d) == Some(cx.insets)
            } else {
                d == want
            };
            if !ok {
                p.drift(format!("{tag}:{wt}_default"), d.as_str(), want.as_str());
            }
        }
        let count = |pat: String| {
            Regex::new(&pat)
                .map(|r| r.find_iter(&text).count())
                .unwrap_or(0)
        };
        let profile = count(format!(r"(?m)^\s*{wt}\s*=\s*\${var}\b"));
        let defaults_w = count(format!(
            r"-NotePropertyName\s+{wt}\s+-NotePropertyValue\s+\${var}\b"
        ));
        if profile == 0 {
            p.drift(
                format!("{tag}:{wt}_profile_writes"),
                "0",
                format!("${}", &c[1]),
            );
        }
        if defaults_w == 0 {
            p.drift(
                format!("{tag}:{wt}_defaults_writes"),
                "0",
                format!("${}", &c[1]),
            );
        }
        p.fact(format!(
            "{tag}:{wt}<-[theme].{ssot_key} default={} writes={profile}+{defaults_w}",
            defaults.join("|")
        ));
    }
}

fn m_vscode_settings(cx: &Cx, path: &Path, p: &mut Probe) {
    let want_density = get_str(&cx.data, "theme.edge.code_server_density")
        .unwrap_or("[theme.edge].code_server_density is not a string");
    let want_modern = get(&cx.data, "theme.edge.code_server_modern_ui")
        .and_then(Value::as_bool)
        .map(|b| b.to_string())
        .unwrap_or_else(|| "[theme.edge].code_server_modern_ui is not a bool".into());
    let mut files = vec![path.to_path_buf()];
    if let Some(projections) = path
        .parent()
        .and_then(Path::parent)
        .and_then(|d| fs::read_dir(d).ok())
    {
        let mut more: Vec<PathBuf> = projections
            .filter_map(|e| e.ok().map(|e| e.path().join("settings.json")))
            .filter(|f| f.is_file() && f != path)
            .collect();
        more.sort();
        files.extend(more);
    }
    for f in files {
        let Some(text) = read(&f, p) else { continue };
        let tag = f
            .parent()
            .and_then(Path::file_name)
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_default();
        let Ok(doc) = serde_json::from_str::<Json>(&text) else {
            p.drift(format!("{tag}:json"), "unparseable", "valid JSON");
            continue;
        };
        let density = doc
            .get(DENSITY_KEY)
            .and_then(Json::as_str)
            .unwrap_or("missing");
        let modern = doc
            .get(MODERN_UI_KEY)
            .map(|v| v.to_string())
            .unwrap_or_else(|| "missing".into());
        if density != want_density {
            p.drift(format!("{tag}:{DENSITY_KEY}"), density, want_density);
        }
        if modern != want_modern {
            p.drift(
                format!("{tag}:{MODERN_UI_KEY}"),
                modern.as_str(),
                want_modern.as_str(),
            );
        }
        p.fact(format!("{tag}:density={density},modernUI={modern}"));
    }
}

/// The artifact's own registry entry (the [dotfiles.registry.*] whose target is it), for its edge_props scope.
fn registry_for<'a>(cx: &'a Cx, artifact: &Path) -> Option<&'a Table> {
    let rel = artifact
        .strip_prefix(&cx.root)
        .ok()?
        .to_string_lossy()
        .into_owned();
    get(&cx.data, "dotfiles.registry")?
        .as_table()?
        .values()
        .filter_map(Value::as_table)
        .find(|s| get_str(s, "target") == Some(rel.as_str()))
}

fn m_code_server(cx: &Cx, path: &Path, p: &mut Probe) {
    let Some(css) = read(path, p) else { return };
    let rules = css_rules(&css);
    let ins = cx.insets;
    let inset_want = |sel: &str, prop: &str| -> Option<(&'static str, u64)> {
        let viewport = sel.ends_with(".xterm-viewport") && sel.contains("terminal-wrapper");
        match (sel, prop) {
            (".monaco-workbench .xterm", "padding-left") => Some(("left", ins.left)),
            (".monaco-workbench .xterm", "padding-top") => Some(("top", ins.top)),
            (".monaco-workbench .xterm", "padding-bottom") => Some(("bottom", ins.bottom)),
            (
                ".monaco-workbench .xterm .xterm-scrollable-element",
                "padding-left" | "margin-left",
            ) => Some(("left", ins.left)),
            (_, "right") if viewport => Some(("right", ins.right)),
            _ => None,
        }
    };
    let reg = registry_for(cx, path);
    let scope = reg
        .and_then(|r| get_str(r, "edge_props.terminal_chrome.scope"))
        .and_then(|s| Regex::new(s).ok());
    let props = reg
        .and_then(|r| get_str(r, "edge_props.terminal_chrome.props"))
        .and_then(|s| Regex::new(&format!(r"\A(?:{s})\z")).ok());
    let chrome = cx.int("theme.edge.code_server_chrome_px");
    let (mut seen, mut chrome_n, mut scrollbar_none) =
        (BTreeMap::<&str, u64>::new(), 0usize, false);
    for (sel, decls) in &rules {
        let scoped = scope.as_ref().is_some_and(|s| s.is_match(sel));
        for (prop, val) in decls {
            if let Some((side, want)) = selectors(sel).find_map(|s| inset_want(s, prop)) {
                let got = css_px(val);
                if got != Some(want as i64) {
                    p.drift(
                        format!("xterm_{prop}"),
                        val.as_str(),
                        format!("{want}px ([theme].padding {side})"),
                    );
                }
                *seen.entry(side).or_default() += 1;
            } else if prop == "display"
                && scoped
                && selectors(sel).any(|s| s.ends_with(".scrollbar"))
            {
                scrollbar_none |= val == "none";
            } else if scoped && props.as_ref().is_some_and(|r| r.is_match(prop)) {
                if let (Some(g), Ok(w)) = (css_px(val), chrome.as_ref()) {
                    chrome_n += 1;
                    if g != *w {
                        let first = selectors(sel).next().unwrap_or(sel).replace(' ', "");
                        p.drift(
                            format!("chrome[{first}]{prop}"),
                            val.as_str(),
                            format!("{w}px"),
                        );
                    }
                }
            }
        }
    }
    for side in ["left", "top", "right", "bottom"] {
        if !seen.contains_key(side) {
            p.drift(
                format!("xterm_inset_{side}"),
                "missing",
                "a rule from [theme].padding",
            );
        }
    }
    if scrollbar_none != cx.hidden() {
        p.drift(
            "terminal_scrollbar",
            if scrollbar_none { "none" } else { "shown" },
            cx.scrollbar.as_str(),
        );
    }
    if let Err(e) = &chrome {
        p.drift("chrome", "?", e.as_str());
    } else if scope.is_none() || props.is_none() {
        p.drift(
            "chrome",
            "unscoped",
            "edge_props.terminal_chrome {scope, props} in its registry entry",
        );
    }
    p.fact(format!(
        "insets={},{},{},{} scrollbar={} chrome_decls={chrome_n}",
        ins.left,
        ins.top,
        ins.right,
        ins.bottom,
        if scrollbar_none { "none" } else { "shown" }
    ));
    if let Some(cs) = &cx.cs_root {
        m_workbench(cx, cs, p);
    }
}

fn m_workbench(cx: &Cx, cs: &Path, p: &mut Probe) {
    static SB: OnceLock<Regex> = OnceLock::new();
    static PM: OnceLock<Regex> = OnceLock::new();
    let js_path = [cs.join(CS_WORKBENCH), cs.join("workbench.js")]
        .into_iter()
        .find(|f| f.is_file())
        .unwrap_or_else(|| cs.join(CS_WORKBENCH));
    let Some(js) = read(&js_path, p) else { return };
    let sb: Vec<i64> = re(&SB, r#"get scrollbarWidth\(\)\{return this\._configurationService\.getValue\("workbench\.experimental\.modernUI"\)===!0\?(\d+):14\}"#)
        .captures_iter(&js)
        .filter_map(|c| c[1].parse().ok())
        .collect();
    let pm: Vec<i64> = re(&PM, r"var ([\w$]+)=4,([\w$]+)=0,([\w$]+)=(\d+),([\w$]+)=0;function [\w$]+\(s\)\{return s\.isModernUICompact\(\)\?([\w$]+):([\w$]+)\}function [\w$]+\(s\)\{return s\.isModernUICompact\(\)\?([\w$]+):([\w$]+)\}")
        .captures_iter(&js)
        .filter(|c| c[6] == c[2] && c[7] == c[1] && c[8] == c[3] && c[9] == c[1])
        .filter_map(|c| c[4].parse().ok())
        .collect();
    for (prop, found, key) in [
        (
            "baked_scrollbarWidth",
            sb,
            "theme.edge.code_server_scrollbar_px",
        ),
        ("baked_perimeter", pm, "theme.edge.code_server_perimeter_px"),
    ] {
        if found.len() > 1 {
            p.drift(prop, "ambiguous", format!("one anchor ([{key}])"));
        } else {
            p.check_int(prop, found.first().copied(), cx.int(key));
        }
    }
}

fn m_gtk4(cx: &Cx, path: &Path, p: &mut Probe) {
    let Some(css) = read(path, p) else { return };
    let rule = css_rules(&css)
        .into_iter()
        .find(|(s, _)| selectors(s).any(|x| x.contains("vte-terminal.padded")));
    let Some((_, decls)) = rule else {
        return p.drift("vte_rule", "missing", "vte-terminal.padded");
    };
    let val = |k: &str| decls.iter().find(|(pp, _)| pp == k).map(|(_, v)| v.clone());
    let ins = cx.insets;
    let want = format!(
        "{}px {}px {}px {}px",
        ins.top, ins.right, ins.bottom, ins.left
    );
    match val("padding").map(|v| {
        v.split_whitespace()
            .map(css_px)
            .collect::<Option<Vec<i64>>>()
    }) {
        Some(Some(v)) => {
            let (t, r, b, l) = match v.as_slice() {
                [a] => (*a, *a, *a, *a),
                [a, b] => (*a, *b, *a, *b),
                [a, b, c] => (*a, *b, *c, *b),
                [a, b, c, d] => (*a, *b, *c, *d),
                _ => (-1, -1, -1, -1),
            };
            if [t, r, b, l] != [ins.top, ins.right, ins.bottom, ins.left].map(|x| x as i64) {
                p.drift("vte_padding", val("padding").unwrap_or_default(), want);
            } else {
                p.fact(format!("vte_padding={t},{r},{b},{l}"));
            }
        }
        _ => p.drift(
            "vte_padding",
            val("padding").unwrap_or_else(|| "missing".into()),
            want,
        ),
    }
    match val("margin").as_deref().and_then(css_px) {
        Some(0) => p.fact("vte_margin=0"),
        _ => p.drift(
            "vte_margin",
            val("margin").unwrap_or_else(|| "missing".into()),
            "0",
        ),
    }
    let name = path
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default();
    let skel = path.with_file_name("gtk.css");
    let import = format!("@import url(\"{name}\")");
    match fs::read_to_string(&skel) {
        Ok(t) if t.contains(&import) => p.fact(format!("gtk.css:{import}")),
        Ok(_) => p.drift("gtk.css_import", "missing", import),
        Err(_) => p.drift("gtk.css", "missing", skel.display().to_string()),
    }
}

fn m_fastfetch(cx: &Cx, path: &Path, p: &mut Probe) {
    static PAD: OnceLock<Regex> = OnceLock::new();
    static KV: OnceLock<Regex> = OnceLock::new();
    let Some(text) = read(path, p) else { return };
    let block = re(&PAD, r#""padding"\s*:\s*\{([^}]*)\}"#)
        .captures(&text)
        .map(|c| c[1].to_string())
        .unwrap_or_default();
    let got: BTreeMap<String, i64> = re(&KV, r#""(\w+)"\s*:\s*(-?\d+)"#)
        .captures_iter(&block)
        .filter_map(|c| Some((c[1].to_string(), c[2].parse().ok()?)))
        .collect();
    let Some(ff) = get(&cx.data, "theme.fastfetch").and_then(Value::as_table) else {
        return p.drift("theme.fastfetch", "missing", "a table");
    };
    let mut n = 0;
    for (k, v) in ff {
        if let Some(side) = k.strip_prefix("logo_padding_") {
            n += 1;
            p.check_int(
                &format!("logo.padding.{side}"),
                got.get(side).copied(),
                v.as_integer()
                    .ok_or(format!("[theme.fastfetch].{k} is not an integer")),
            );
        }
    }
    if n == 0 {
        p.drift(
            "logo_padding",
            "unmeasured",
            "[theme.fastfetch].logo_padding_*",
        );
    }
}

fn m_wm(cx: &Cx, path: &Path, p: &mut Probe) {
    let keys = [
        ("wm_gaps_inner_px", "gaps_in", "gaps inner"),
        ("wm_gaps_outer_px", "gaps_out", "gaps outer"),
        ("wm_border_px", "border_size", "default_border pixel"),
    ];
    let sway = path
        .parent()
        .and_then(Path::parent)
        .map(|d| d.join("sway/config"))
        .unwrap_or_default();
    for (file, hypr) in [(path.to_path_buf(), true), (sway, false)] {
        let Some(text) = read(&file, p) else { continue };
        for (ssot, h, s) in keys {
            let (k, pat) = if hypr {
                (h, format!(r"(?m)^\s*{h}\s*=\s*(-?\d+)\s*$"))
            } else {
                (s, format!(r"(?m)^\s*{s}\s+(-?\d+)\s*$"))
            };
            let got = Regex::new(&pat)
                .ok()
                .and_then(|r| r.captures(&text))
                .and_then(|c| c[1].parse().ok());
            let label = format!(
                "{}:{}",
                if hypr { "hyprland" } else { "sway" },
                k.replace(' ', "_")
            );
            p.check_int(&label, got, cx.int(&format!("theme.edge.{ssot}")));
        }
    }
}

fn m_alacritty(cx: &Cx, path: &Path, p: &mut Probe) {
    let Some(text) = read(path, p) else { return };
    let Ok(doc) = text.parse::<Table>() else {
        return p.drift("toml", "unparseable", "valid TOML");
    };
    let ins = cx.insets;
    for (axis, a, b) in [("x", ins.left, ins.right), ("y", ins.top, ins.bottom)] {
        let got = get(&doc, &format!("window.padding.{axis}")).and_then(Value::as_integer);
        let want = if a == b {
            Ok(a as i64)
        } else {
            Err(format!("symmetric [theme].padding ({a} vs {b})"))
        };
        p.check_int(&format!("padding.{axis}"), got, want);
    }
}

fn m_portal(cx: &Cx, path: &Path, p: &mut Probe) {
    let Some(css) = read(path, p) else { return };
    let rules = css_rules(&css);
    let find = |suffix: &str, prop: &str| {
        rules
            .iter()
            .filter(|(s, _)| selectors(s).any(|x| x.ends_with(suffix)))
            .flat_map(|(_, d)| d.iter())
            .find(|(pp, _)| pp == prop)
            .map(|(_, v)| v.clone())
    };
    for prop in ["border-width", "border-radius"] {
        p.check_int(
            &format!("embed-box_{prop}"),
            find(".embed-box", prop).as_deref().and_then(css_px),
            cx.int("theme.edge.portal_term_chrome_px"),
        );
    }
    let bleed = ["margin-left", "margin-right", "margin-bottom"]
        .iter()
        .all(|m| find(" .embed", m).is_some_and(|v| v.replace(' ', "").starts_with("calc(-1*")));
    match get(&cx.data, "theme.edge.portal_term_bleed").and_then(Value::as_bool) {
        Some(w) if w == bleed => p.fact(format!("bleed={bleed}")),
        Some(w) => p.drift("bleed", bleed.to_string(), w.to_string()),
        None => p.drift(
            "bleed",
            bleed.to_string(),
            "[theme.edge].portal_term_bleed is not a bool",
        ),
    }
}

fn m_ttyd(cx: &Cx, path: &Path, p: &mut Probe) {
    static TTYD_PAD: OnceLock<Regex> = OnceLock::new();
    let Some(text) = read(path, p) else { return };
    let Ok(pin) = serde_json::from_str::<Json>(&text) else {
        return p.drift("json", "unparseable", "valid JSON");
    };
    let field = |k: &str| {
        pin.get(k)
            .and_then(Json::as_str)
            .unwrap_or("missing")
            .to_string()
    };
    for (k, ssot) in [
        ("version", "ttyd.version"),
        ("source_sha256", "ttyd.page_sha256"),
    ] {
        match get_str(&cx.data, ssot) {
            Some(w) if w == field(k) => {}
            w => p.drift(k, field(k), w.unwrap_or("missing")),
        }
    }
    let rule = field("terminal_rule");
    let got = re(&TTYD_PAD, r"padding:(\d+)px\}$")
        .captures(&rule)
        .and_then(|c| c[1].parse().ok());
    p.check_int(
        "terminal_padding",
        got,
        cx.int("theme.edge.ttyd_padding_px"),
    );
    match fs::read_to_string(cx.root.join(PORTAL_EDGE)) {
        Ok(src)
            if src
                .lines()
                .any(|l| l.starts_with("TTYD_ANCHOR = ") && l.contains(".terminal{")) => {}
        _ => p.drift(
            "ttyd_anchor",
            "missing",
            format!("TTYD_ANCHOR in {PORTAL_EDGE}"),
        ),
    }
    match fs::read_to_string(cx.root.join(TTYD_LAUNCHER)) {
        Ok(l) if l.contains("\"-I\"") && l.contains("page_path") => p.fact("launcher=-I"),
        Ok(_) => p.drift("launcher", "no -I page_path", "-I [ttyd].page_path"),
        Err(_) => p.drift("launcher", "missing", TTYD_LAUNCHER),
    }
}

/// Printed lines and the exit status (0 clean, 1 DRIFT, 2 unclassified, 3 unusable input).
pub struct Report {
    pub lines: Vec<String>,
    pub code: u8,
}

pub fn run(root: &Path, bootstrap: Option<&Path>, cs_root: Option<&Path>) -> Report {
    let fatal = |m: String| Report {
        lines: vec![format!("error: {m}")],
        code: 3,
    };
    let data = match load_vendor(root) {
        Ok(d) => d,
        Err(e) => return fatal(e),
    };
    let padding = get(&data, "theme.padding")
        .map(|v| {
            v.as_str()
                .map(str::to_string)
                .unwrap_or_else(|| v.to_string())
        })
        .unwrap_or_default();
    let Some(insets) = parse_padding(&padding) else {
        return fatal(format!(
            "[theme].padding: '{padding}' is not a non-negative integer WT padding"
        ));
    };
    let scrollbar = get_str(&data, "theme.scrollbar_state")
        .unwrap_or_default()
        .to_string();
    if !SCROLLBAR_STATES.contains(&scrollbar.as_str()) {
        return fatal(format!(
            "[theme].scrollbar_state: '{scrollbar}' is not one of {}",
            SCROLLBAR_STATES.join(", ")
        ));
    }
    let Some(reach) = get(&data, "theme.edge.reach")
        .and_then(Value::as_table)
        .cloned()
    else {
        return fatal("[theme.edge.reach] is missing".into());
    };
    let cx = Cx {
        root: root.to_path_buf(),
        bootstrap: bootstrap.map(Path::to_path_buf),
        cs_root: cs_root.map(Path::to_path_buf),
        data,
        insets,
        padding,
        scrollbar,
    };
    let mut edge: BTreeMap<String, usize> = get(&cx.data, "dotfiles.registry")
        .and_then(Value::as_table)
        .map(|r| {
            r.iter()
                .filter(|(_, s)| {
                    get(s.as_table().unwrap_or(&Table::new()), "edge").and_then(Value::as_bool)
                        == Some(true)
                })
                .map(|(n, _)| (n.clone(), 0))
                .collect()
        })
        .unwrap_or_default();
    let mut rep = Report {
        lines: Vec::new(),
        code: 0,
    };
    let mut unclassified = Vec::new();
    for (key, entry) in &reach {
        let Some(e) = entry.as_table() else {
            unclassified.push(format!("unclassified reach {key}"));
            continue;
        };
        let level = get_str(e, "reach").unwrap_or("");
        let why = get_str(e, "why").unwrap_or("");
        if !REACHES.contains(&level) {
            unclassified.push(format!("unclassified reach {key}"));
            continue;
        }
        if let Some(reg) = get_str(e, "registry") {
            match edge.get_mut(reg) {
                Some(n) => *n += 1,
                None => unclassified.push(format!("unclassified reach {key}")),
            }
        }
        let mut probe = Probe::default();
        match (level, get_str(e, "artifact")) {
            ("none" | "n/a", _) => probe.fact(why),
            ("partial", None) => probe.fact("unmeasured (no artifact)"),
            (_, None) => probe.fact(format!("unmeasured (no artifact): {why}")),
            (_, Some(art)) => match measurer(art) {
                None => unclassified.push(format!("unclassified artifact {key} {art}")),
                Some(m) => {
                    let bootstrap_only = get_str(e, "artifact_scope") == Some("bootstrap");
                    let copies: Vec<PathBuf> = std::iter::once(cx.root.join(art))
                        .filter(|_| !bootstrap_only)
                        .chain(cx.bootstrap.iter().map(|b| b.join(art)))
                        .filter(|f| f.is_file())
                        .collect();
                    let targets = if art.ends_with(".ps1") {
                        copies
                    } else {
                        copies.into_iter().take(1).collect()
                    };
                    // Runtime staging directories cannot turn a bootstrap-only
                    // artifact into an alleged missing MiOS artifact.
                    let mios_side =
                        !bootstrap_only && cx.root.join(art).parent().is_some_and(Path::is_dir);
                    if targets.is_empty() && cx.bootstrap.is_none() && !mios_side {
                        probe.fact(format!(
                            "unmeasured: {art} is not in --root; pass --bootstrap"
                        ));
                    } else if targets.is_empty() {
                        probe.drift("artifact", "missing", art);
                    }
                    for t in &targets {
                        m(&cx, t, &mut probe);
                    }
                }
            },
        }
        let mut line = format!("edge {key} {level} {}", probe.facts.join(" "));
        if let Some(lane) = get_str(e, "pending") {
            line.push_str(&format!(" pending={lane}"));
            if level == "full" {
                probe.drift(
                    "pending",
                    lane,
                    "absent on reach=full (the owed lane has not delivered)",
                );
            }
        }
        if level == "partial" {
            line.push_str(&format!(" residual: {why}"));
        }
        rep.lines.push(line.trim_end().to_string());
        for (prop, got, want) in probe.drifts {
            rep.lines
                .push(format!("edge {key} DRIFT {prop}={got} want={want}"));
            rep.code = rep.code.max(1);
        }
    }
    unclassified.extend(
        edge.iter()
            .filter(|(_, n)| **n != 1)
            .map(|(name, _)| format!("unclassified registry {name}")),
    );
    if !unclassified.is_empty() {
        rep.code = 2;
        rep.lines.extend(unclassified);
    }
    rep
}

fn main() -> ExitCode {
    let (mut root, mut bootstrap, mut cs_root) = (
        PathBuf::from("."),
        std::env::var_os("MIOS_BOOTSTRAP_ROOT").map(PathBuf::from),
        None,
    );
    let mut args = std::env::args().skip(1);
    while let Some(a) = args.next() {
        let slot = match a.as_str() {
            "--root" => &mut root,
            "--bootstrap" => bootstrap.insert(PathBuf::new()),
            "--code-server-root" => cs_root.insert(PathBuf::new()),
            "-h" | "--help" => {
                print!("{USAGE}");
                return ExitCode::SUCCESS;
            }
            _ => {
                eprint!("mios-edge-status: unknown argument {a}\n{USAGE}");
                return ExitCode::from(3);
            }
        };
        match args.next() {
            Some(v) => *slot = PathBuf::from(v),
            None => {
                eprint!("mios-edge-status: {a} needs a value\n{USAGE}");
                return ExitCode::from(3);
            }
        }
    }
    let rep = run(&root, bootstrap.as_deref(), cs_root.as_deref());
    for l in &rep.lines {
        println!("{l}");
    }
    ExitCode::from(rep.code)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn repo() -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../..")
    }

    fn scratch(name: &str) -> PathBuf {
        let d =
            std::env::temp_dir().join(format!("mios-edge-status-{}-{name}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        fs::create_dir_all(d.join("usr/share/mios"))
            .unwrap_or_else(|e| unreachable!("scratch {e}"));
        d
    }

    fn ssot_with(dir: &Path, edit: impl Fn(&str) -> String) {
        let text =
            fs::read_to_string(repo().join(SSOT)).unwrap_or_else(|e| unreachable!("ssot {e}"));
        fs::write(dir.join(SSOT), edit(&text)).unwrap_or_else(|e| unreachable!("write {e}"));
    }

    #[test]
    fn padding_cases_parity() {
        let tsv =
            fs::read_to_string(repo().join("usr/share/mios/theme/fixtures/edge/padding-cases.tsv"))
                .unwrap_or_default();
        let mut n = 0;
        for row in tsv.lines().filter(|l| !l.starts_with('#') && !l.is_empty()) {
            let cols: Vec<&str> = row.split('\t').collect();
            let input = cols[0].replace("<empty>", "").replace("<LF>", "\n");
            let want = match cols[1..] {
                ["ERR"] => None,
                [l, t, r, b] => Some(Insets {
                    left: l.parse().unwrap_or(99),
                    top: t.parse().unwrap_or(99),
                    right: r.parse().unwrap_or(99),
                    bottom: b.parse().unwrap_or(99),
                }),
                _ => unreachable!("bad row {row:?}"),
            };
            assert_eq!(parse_padding(&input), want, "row {row:?}");
            n += 1;
        }
        assert!(n >= 10, "padding-cases.tsv gave only {n} rows");
    }

    #[test]
    fn committed_tree_is_clean() {
        let rep = run(&repo(), None, None);
        assert_eq!(rep.code, 0, "{}", rep.lines.join("\n"));
        let keys = get(
            &load_vendor(&repo()).unwrap_or_default(),
            "theme.edge.reach",
        )
        .and_then(Value::as_table)
        .map(Table::len)
        .unwrap_or(0);
        assert_eq!(rep.lines.len(), keys, "one line per reach key");
    }

    #[test]
    fn bootstrap_scope_ignores_incidental_staging_but_measures_the_bootstrap() {
        let root = scratch("bootstrap-scope");
        ssot_with(&root, str::to_string);
        assert!(fs::create_dir_all(root.join("field/lib")).is_ok());
        let without_bootstrap = run(&root, None, None);
        assert!(without_bootstrap.lines.iter().any(|line| line
            .starts_with("edge wt-backend full unmeasured: field/lib/Get-MiOS-Backend.ps1")));
        let missing_bootstrap = scratch("missing-bootstrap");
        let missing = run(&root, Some(&missing_bootstrap), None);
        assert!(missing.lines.iter().any(|line| line
            == "edge wt-backend DRIFT artifact=missing want=field/lib/Get-MiOS-Backend.ps1"));
        // A MiOS staging copy must not disguise the missing bootstrap artifact.
        assert!(fs::write(root.join("field/lib/Get-MiOS-Backend.ps1"), "staging copy").is_ok());
        let staged = run(&root, Some(&missing_bootstrap), None);
        assert!(staged.lines.iter().any(|line| line
            == "edge wt-backend DRIFT artifact=missing want=field/lib/Get-MiOS-Backend.ps1"));
        let _ = fs::remove_dir_all(&root);
        let _ = fs::remove_dir_all(&missing_bootstrap);
    }

    #[test]
    fn pending_on_full_and_ssot_density_exit_1() {
        let d = scratch("pend");
        ssot_with(&d, |t| {
            t.replacen(
                "hyprland/hyprland.conf\" }",
                "hyprland/hyprland.conf\", pending = \"owed-lane\" }",
                1,
            )
            .replacen(
                "code_server_density      = \"compact\"",
                "code_server_density      = \"spacious\"",
                1,
            )
        });
        for f in [
            ".dotfiles/code-server/settings.json",
            ".dotfiles/vscode/settings.json",
        ] {
            fs::create_dir_all(d.join(f).parent().unwrap_or(&d)).unwrap_or_default();
            fs::copy(repo().join(f), d.join(f)).unwrap_or_default();
        }
        let rep = run(&d, None, None);
        let _ = fs::remove_dir_all(&d);
        let has = |p: &str| rep.lines.iter().any(|l| l.starts_with(p));
        assert!(
            has("edge hyprland-sway DRIFT pending=owed-lane want=absent on reach=full"),
            "{}",
            rep.lines.join("\n")
        );
        assert!(has("edge vscode-settings DRIFT code-server:window.density.layout=compact want=spacious"), "{}", rep.lines.join("\n"));
        assert_eq!(rep.code, 1);
    }

    #[test]
    fn wt_fixture_drift_exits_1() {
        let d = scratch("wt");
        ssot_with(&d, str::to_string);
        let rel = "usr/share/mios/theme/fixtures/windows-terminal-settings.expected.json";
        fs::create_dir_all(d.join(rel).parent().unwrap_or(&d)).unwrap_or_default();
        let text = fs::read_to_string(repo().join(rel))
            .unwrap_or_default()
            .replacen("\"padding\": \"0\"", "\"padding\": \"8, 8, 8, 8\"", 1);
        fs::write(d.join(rel), text).unwrap_or_default();
        let rep = run(&d, None, None);
        let _ = fs::remove_dir_all(&d);
        assert!(
            rep.lines
                .iter()
                .any(|l| l == "edge wt-projection DRIFT padding=8, 8, 8, 8 want=0"),
            "{}",
            rep.lines.join("\n")
        );
        assert_eq!(rep.code, 1);
    }

    #[test]
    fn deleted_reach_entry_is_unclassified_registry() {
        let d = scratch("reg");
        ssot_with(&d, |t| {
            t.lines()
                .filter(|l| !l.starts_with("wt-projection "))
                .collect::<Vec<_>>()
                .join("\n")
        });
        let rep = run(&d, None, None);
        let _ = fs::remove_dir_all(&d);
        assert!(
            rep.lines
                .iter()
                .any(|l| l == "unclassified registry windows-terminal"),
            "{}",
            rep.lines.join("\n")
        );
        assert_eq!(rep.code, 2);
    }

    #[test]
    fn registry_field_naming_no_edge_surface_is_unclassified_reach() {
        let d = scratch("reach");
        ssot_with(&d, |t| {
            t.replacen("registry = \"windows-terminal\"", "registry = \"btop\"", 1)
        });
        let rep = run(&d, None, None);
        let _ = fs::remove_dir_all(&d);
        assert!(
            rep.lines
                .iter()
                .any(|l| l == "unclassified reach wt-projection"),
            "{}",
            rep.lines.join("\n")
        );
        assert_eq!(rep.code, 2);
    }

    #[test]
    fn baked_workbench_is_measured() {
        let js = |sb: u32, pm: u32| {
            format!(
                "x;get scrollbarWidth(){{return this._configurationService.getValue(\"workbench.experimental.modernUI\")===!0?{sb}:14}};var a=4,b=0,c={pm},d=0;function f(s){{return s.isModernUICompact()?b:a}}function g(s){{return s.isModernUICompact()?c:a}};"
            )
        };
        for (sb, pm, want) in [
            (0, 0, None),
            (10, 0, Some("baked_scrollbarWidth=10 want=0")),
            (0, 4, Some("baked_perimeter=4 want=0")),
        ] {
            let d = scratch(&format!("cs{sb}{pm}"));
            fs::write(d.join("workbench.js"), js(sb, pm)).unwrap_or_default();
            let rep = run(&repo(), None, Some(&d));
            let _ = fs::remove_dir_all(&d);
            let drift: Vec<&String> = rep.lines.iter().filter(|l| l.contains("DRIFT")).collect();
            match want {
                None => assert!(drift.is_empty(), "{drift:?}"),
                Some(w) => assert!(drift.iter().any(|l| l.ends_with(w)), "{w}: {drift:?}"),
            }
        }
    }
}
