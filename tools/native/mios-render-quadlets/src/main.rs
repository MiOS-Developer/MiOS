// AI-hint: SSOT-driven Quadlet placeholder renderer; replaces stage 34's envsubst + bash-regex pair with a real parser.
// AI-related: usr/share/mios/mios.toml, automation/34-render-quadlets.sh, tools/native/mios-resolver/src/expand.rs

#![forbid(unsafe_code)]
#![warn(clippy::unwrap_used, clippy::expect_used, clippy::panic)]

use mios_resolver::expand;

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::process::ExitCode;

const SSOT: &str = "usr/share/mios/mios.toml";
const USAGE: &str = "usage: mios-render-quadlets [--root DIR] [--check]\n";

fn die(msg: &str) -> ExitCode {
    eprintln!("mios-render-quadlets: {msg}");
    ExitCode::from(2)
}

/// Every knob comes from `[build.quadlet_render]`. Nothing here is a literal:
/// the two bash renderers each carried their own hardcoded list and had already
/// drifted apart by fourteen names (task_977).
#[derive(Debug)]
struct Config {
    dirs: Vec<String>,
    extensions: Vec<String>,
    max_depth: usize,
    runtime_ref_directives: Vec<String>,
}

fn strings(v: Option<&toml::Value>, key: &str) -> Result<Vec<String>, String> {
    let arr = v
        .and_then(|x| x.as_array())
        .ok_or_else(|| format!("[build.quadlet_render].{key} is missing or not an array"))?;
    let mut out = Vec::with_capacity(arr.len());
    for e in arr {
        let s = e
            .as_str()
            .ok_or_else(|| format!("[build.quadlet_render].{key} holds a non-string entry"))?;
        if s.trim().is_empty() {
            return Err(format!("[build.quadlet_render].{key} holds an empty entry"));
        }
        out.push(s.trim().to_string());
    }
    if out.is_empty() {
        // An empty list would render nothing and exit 0 -- the Empty-Set Pass.
        return Err(format!(
            "[build.quadlet_render].{key} is empty -- refusing to run rather than silently rendering nothing"
        ));
    }
    Ok(out)
}

fn load_config(root: &Path) -> Result<Config, String> {
    let path = root.join(SSOT);
    let text = std::fs::read_to_string(&path)
        .map_err(|e| format!("{SSOT} could not be read ({e}) -- nothing was rendered"))?;
    let doc: toml::Value = text
        .parse()
        .map_err(|e| format!("{SSOT} did not parse ({e}) -- nothing was rendered"))?;
    let qr = doc
        .get("build")
        .and_then(|b| b.get("quadlet_render"))
        .ok_or_else(|| format!("{SSOT} has no [build.quadlet_render] -- nothing was rendered"))?;

    let max_depth = qr
        .get("max_depth")
        .and_then(|d| d.as_integer())
        .ok_or_else(|| {
            "[build.quadlet_render].max_depth is missing or not an integer".to_string()
        })?;
    if max_depth < 1 {
        return Err("[build.quadlet_render].max_depth must be at least 1".into());
    }

    Ok(Config {
        dirs: strings(qr.get("dirs"), "dirs")?,
        extensions: strings(qr.get("extensions"), "extensions")?,
        max_depth: max_depth as usize,
        runtime_ref_directives: strings(
            qr.get("runtime_ref_directives"),
            "runtime_ref_directives",
        )?,
    })
}

/// The SSOT exports map every other native consumer resolves through, with the
/// process environment overlaid on top so an operator export still wins. The
/// overlay is explicit here rather than hidden inside the expander, so what
/// outranks what is visible and testable.
fn ssot_exports(root: &Path) -> BTreeMap<String, String> {
    let fig = mios_resolver::layers::create_figment(Some(root));
    match fig.extract::<toml::Value>() {
        Ok(mut merged) => {
            mios_resolver::db_overlay::maybe_apply_db_overlay(&mut merged, false);
            let offset = merged
                .get("ports")
                .and_then(|p| p.get("stack_id"))
                .and_then(|v| {
                    v.as_integer()
                        .or_else(|| v.as_str().and_then(|s| s.parse::<i64>().ok()))
                })
                .map(|id| id * 10000)
                .unwrap_or(0);
            let mut map = mios_resolver::emit::build_exports_map(&merged, offset);
            overlay_env(&mut map);
            map
        }
        Err(e) => {
            eprintln!(
                "mios-render-quadlets: WARNING: SSOT exports unavailable ({e}); \
                 placeholders will be reported unresolved rather than blanked"
            );
            let mut map = BTreeMap::new();
            overlay_env(&mut map);
            map
        }
    }
}

/// A non-empty `MIOS_*` in the process environment outranks the SSOT, which is
/// how an operator override reaches the bake. Only `MIOS_*`: anything else is
/// not ours to substitute.
fn overlay_env(map: &mut BTreeMap<String, String>) {
    overlay_from(map, std::env::vars())
}

/// Split out so the predicate itself is testable: a test that inserted into the
/// map by hand would assert its own arrangement, not this.
fn overlay_from<I>(map: &mut BTreeMap<String, String>, vars: I)
where
    I: IntoIterator<Item = (String, String)>,
{
    for (k, v) in vars {
        if k.starts_with("MIOS_") && !v.is_empty() {
            map.insert(k, v);
        }
    }
}

/// A unit declaring Environment=/EnvironmentFile= **in `[Service]`** owns its
/// Exec refs (task_977).
///
/// The section is the whole point. In a `.container` Quadlet,
/// `[Container] Environment=` becomes podman's `--env`: it populates the
/// environment INSIDE the container and has no bearing on how systemd expands
/// `${VAR}` in the generated unit's Exec lines. Only `[Service]` gives systemd
/// something to expand against. Scanning the file without regard to section
/// therefore granted runtime ownership on the strength of a directive that
/// cannot confer it -- six shipped units declare env solely in `[Container]`
/// (mios-k3s, mios-node, mios-guacamole, mios-otelcol, mios-forgejo-runner and
/// mios-llm-worker@), and each would have had any Exec placeholder left
/// unbaked, to be expanded at runtime from an environment that never contains
/// it. Latent only because none of the six currently carries a `${MIOS_*}` on a
/// runtime-ref directive; mios-k3s.container comes closest, and its placeholder
/// sits on `Image=`, which is not one.
///
/// Every runtime_ref_directive is an `Exec*`, and those live only in
/// `[Service]`, so requiring the declaration to share that section is enough.
fn declares_unit_environment(content: &str) -> bool {
    let mut in_service = false;
    for line in content.lines() {
        let t = line.trim();
        if t.starts_with('[') && t.ends_with(']') {
            in_service = t == "[Service]";
            continue;
        }
        if !in_service {
            continue;
        }
        if t.starts_with("Environment=") || t.starts_with("EnvironmentFile=") {
            return true;
        }
    }
    false
}

fn directive_of(line: &str) -> Option<&str> {
    let t = line.trim_start();
    let eq = t.find('=')?;
    Some(t[..eq].trim())
}

fn is_runtime_ref_line(line: &str, cfg: &Config) -> bool {
    match directive_of(line) {
        Some(d) => cfg.runtime_ref_directives.iter().any(|r| r == d),
        None => false,
    }
}

struct FileOutcome {
    changed: bool,
    rendered: String,
    unresolved: Vec<(usize, String)>,
}

/// A directive continued with a trailing backslash owns its continuation lines.
/// mios-agents.service writes its whole `podman run` invocation that way, so
/// judging each physical line on its own left thirteen genuine runtime
/// references unprotected -- caught by rendering the real unit into a fixture.
fn continues(line: &str) -> bool {
    line.trim_end().ends_with('\\')
}

fn is_comment(line: &str) -> bool {
    let t = line.trim_start();
    t.starts_with('#') || t.starts_with(';')
}

/// Render one file line by line so the runtime-ref rule can be applied per
/// directive rather than per file.
fn render_file(content: &str, cfg: &Config, ssot: &BTreeMap<String, String>) -> FileOutcome {
    let unit_env = declares_unit_environment(content);
    let mut out = String::with_capacity(content.len());
    let mut unresolved = Vec::new();
    let mut in_runtime_ref = false;
    for (i, line) in content.split_inclusive('\n').enumerate() {
        // A comment is documentation, not configuration. Rendering one destroys
        // the prose (mios-agents.service documents `${VAR}` in its own header)
        // and reporting it is a false positive.
        if is_comment(line) {
            out.push_str(line);
            continue;
        }
        if !in_runtime_ref {
            in_runtime_ref = unit_env && is_runtime_ref_line(line, cfg);
        }
        let protected = in_runtime_ref;
        if !continues(line) {
            in_runtime_ref = false;
        }
        // A protected line still gets its `${VAR:-default}` forms baked:
        // systemd does not expand that form anywhere, so leaving one ships a
        // unit that resolves it to empty. mios-agents.service documents the
        // distinction in its own header and carries one on line 45.
        let e = if protected {
            expand::expand_mode(line, ssot, expand::Mode::DefaultsOnly)
        } else {
            expand::expand(line, ssot)
        };
        for name in e.unresolved {
            unresolved.push((i + 1, name));
        }
        out.push_str(&e.text);
    }
    FileOutcome {
        changed: out != content,
        rendered: out,
        unresolved,
    }
}

/// `${NAME:-...}` forms still present after rendering, as (line, NAME).
/// systemd expands that form nowhere, so one that survives ships as literal
/// text (the forgejo runner label read `fedora-${FEDORA_VERSION:-44}`).
/// `$${...}` is systemd's escape that hands a literal to a shell, so it is
/// not a survivor; comments are documentation.
fn surviving_defaults(rendered: &str) -> Vec<(usize, String)> {
    let mut out = Vec::new();
    for (i, line) in rendered.lines().enumerate() {
        if is_comment(line) {
            continue;
        }
        let b = line.as_bytes();
        let mut j = 0;
        while let Some(off) = line[j..].find("${") {
            let at = j + off;
            j = at + 2;
            if at > 0 && b[at - 1] == b'$' {
                continue;
            }
            let rest = &line[at + 2..];
            let name: String = rest
                .chars()
                .take_while(|c| c.is_ascii_alphanumeric() || *c == '_')
                .collect();
            if !name.is_empty() && rest[name.len()..].starts_with(":-") {
                out.push((i + 1, name));
            }
        }
    }
    out
}

fn main() -> ExitCode {
    let mut root = PathBuf::from(".");
    let mut check = false;
    let mut args = std::env::args().skip(1);
    while let Some(a) = args.next() {
        match a.as_str() {
            "--root" => match args.next() {
                Some(v) => root = PathBuf::from(v),
                None => return die("--root needs a directory"),
            },
            "--check" => check = true,
            "-h" | "--help" => {
                print!("{USAGE}");
                return ExitCode::SUCCESS;
            }
            other => return die(&format!("unknown argument '{other}'\n{USAGE}")),
        }
    }

    let cfg = match load_config(&root) {
        Ok(c) => c,
        Err(e) => return die(&e),
    };
    let ssot = ssot_exports(&root);

    let mut scanned = 0usize;
    let mut changed = 0usize;
    let mut problems: Vec<String> = Vec::new();

    for dir in &cfg.dirs {
        let abs = root.join(dir.trim_start_matches('/'));
        if !abs.is_dir() {
            continue;
        }
        for entry in walkdir::WalkDir::new(&abs)
            .max_depth(cfg.max_depth)
            .into_iter()
            .filter_map(Result::ok)
        {
            if !entry.file_type().is_file() {
                continue;
            }
            let p = entry.path();
            let ext = match p.extension().and_then(|e| e.to_str()) {
                Some(e) => e,
                None => continue,
            };
            if !cfg.extensions.iter().any(|x| x == ext) {
                continue;
            }
            let content = match std::fs::read_to_string(p) {
                Ok(c) => c,
                Err(_) => continue, // not UTF-8 text; not ours to render
            };
            if !content.contains("${MIOS_") {
                // Nothing to render, but a non-MIOS default form is still a
                // unit systemd will read literally.
                for (line, name) in surviving_defaults(&content) {
                    problems.push(format!(
                        "{}:{line} carries ${{{name}:-...}}, which systemd never expands -- \
                         reference the SSOT (${{MIOS_...}}) or escape it ($${{...}}) for a shell",
                        p.display()
                    ));
                }
                continue;
            }
            scanned += 1;
            let outcome = render_file(&content, &cfg, &ssot);
            for (line, name) in surviving_defaults(&outcome.rendered) {
                problems.push(format!(
                    "{}:{line} still carries ${{{name}:-...}} after rendering, which systemd \
                     never expands -- reference the SSOT (${{MIOS_...}}) or escape it ($${{...}}) for a shell",
                    p.display()
                ));
            }
            for (line, name) in &outcome.unresolved {
                problems.push(format!(
                    "{}:{line} floats on ${{{name}}}, which resolved to nothing -- it is not in the \
                     environment nor the SSOT exports, and the unit declares no Environment= or \
                     EnvironmentFile= that could supply it at runtime",
                    p.display()
                ));
            }
            if outcome.changed {
                changed += 1;
                if !check {
                    if let Err(e) = std::fs::write(p, &outcome.rendered) {
                        return die(&format!("{} could not be written ({e})", p.display()));
                    }
                }
            }
        }
    }

    if scanned == 0 {
        return die("no file carrying a ${MIOS_ placeholder was found -- the scan matched nothing, which is not the same as having nothing to do");
    }
    if !problems.is_empty() {
        for p in &problems {
            eprintln!("mios-render-quadlets: {p}");
        }
        eprintln!(
            "mios-render-quadlets: {} unresolved or unexpandable placeholder(s) across {scanned} file(s)",
            problems.len()
        );
        return ExitCode::from(1);
    }
    if check {
        // Deliberately NOT "is the tree already rendered". Stage 34 renders
        // in place at bake; tracked files are TEMPLATES and keep their
        // placeholders. The gated property is that every placeholder
        // RESOLVES -- unresolved ones were reported above.
        println!(
            "mios-render-quadlets: OK: {scanned} file(s) scanned, every placeholder \
             resolves ({changed} would change at bake)"
        );
        return ExitCode::SUCCESS;
    }
    println!("mios-render-quadlets: checked {scanned} file(s), updated {changed}, unchanged {}; no unresolved placeholders", scanned - changed);
    ExitCode::SUCCESS
}

#[cfg(test)]
// Fixture setup panics on failure by design; the crate bans that in production paths.
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;

    fn cfg() -> Config {
        Config {
            dirs: vec!["etc/mios".into()],
            extensions: vec!["service".into(), "toml".into()],
            max_depth: 2,
            runtime_ref_directives: vec!["ExecStart".into(), "ExecStartPre".into()],
        }
    }

    fn ssot(pairs: &[(&str, &str)]) -> BTreeMap<String, String> {
        pairs
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect()
    }

    /// mios-agents.service writes its whole podman invocation as one
    /// backslash-continued ExecStart. Judging each physical line alone left
    /// thirteen genuine runtime references unprotected -- this is that bug.
    #[test]
    fn a_continued_exec_line_keeps_its_runtime_refs() {
        let unit = "[Service]\nEnvironment=MIOS_X=1\nExecStart=/bin/podman run \\\n  --env A=${MIOS_X} \\\n  --env B=${MIOS_Y}\n";
        let out = render_file(unit, &cfg(), &ssot(&[("MIOS_X", "9"), ("MIOS_Y", "9")]));
        assert!(!out.changed, "runtime refs must survive: {}", out.rendered);
        assert!(out.rendered.contains("${MIOS_X}"), "{}", out.rendered);
        assert!(out.rendered.contains("${MIOS_Y}"), "{}", out.rendered);
        assert!(out.unresolved.is_empty(), "{:?}", out.unresolved);
    }

    /// systemd cannot expand `${VAR:-default}` ANYWHERE -- replace_env_full is
    /// called with flags=0, and env-util.c treats `:` as unsupported syntax and
    /// does no replacement. So a protected line still gets its :- forms baked,
    /// while its bare refs are left. mios-agents.service:45 carries exactly one
    /// and its own header records the failure: code-server dies "Invalid URL".
    #[test]
    fn a_protected_line_still_bakes_its_default_forms() {
        let unit = "[Service]\nEnvironment=MIOS_X=1\nExecStart=/bin/cs \\\n  --env A=${MIOS_X} \\\n  --bind-addr 127.0.0.1:${MIOS_PORTS_CODE_SERVER:-8900}\n";
        let out = render_file(
            unit,
            &cfg(),
            &ssot(&[("MIOS_X", "9"), ("MIOS_PORTS_CODE_SERVER", "8900")]),
        );
        assert!(
            out.rendered.contains("--env A=${MIOS_X}"),
            "bare ref must survive: {}",
            out.rendered
        );
        assert!(
            out.rendered.contains("127.0.0.1:8900"),
            ":- form must be baked: {}",
            out.rendered
        );
        assert!(
            !out.rendered.contains(":-"),
            "no :- may survive: {}",
            out.rendered
        );
        assert!(out.unresolved.is_empty(), "{:?}", out.unresolved);
    }

    /// The continuation must END. A later non-Exec directive is fair game.
    #[test]
    fn protection_stops_when_the_continuation_stops() {
        let unit = "[Service]\nEnvironment=MIOS_X=1\nExecStart=/bin/true \\\n  --env A=${MIOS_X}\nListenStream=${MIOS_PORT}\n";
        let out = render_file(
            unit,
            &cfg(),
            &ssot(&[("MIOS_X", "9"), ("MIOS_PORT", "8320")]),
        );
        assert!(
            out.rendered.contains("--env A=${MIOS_X}"),
            "{}",
            out.rendered
        );
        assert!(
            out.rendered.contains("ListenStream=8320"),
            "{}",
            out.rendered
        );
    }

    /// A unit with NO Environment=/EnvironmentFile= owns nothing at runtime, so
    /// even an Exec line must be rendered -- otherwise systemd expands it to
    /// empty. This is the half that keeps the rule from becoming a blanket skip.
    #[test]
    fn without_a_unit_environment_even_exec_lines_render() {
        let unit = "[Service]\nExecStart=/bin/true --port=${MIOS_PORT}\n";
        let out = render_file(unit, &cfg(), &ssot(&[("MIOS_PORT", "8320")]));
        assert!(out.changed);
        assert!(out.rendered.contains("--port=8320"), "{}", out.rendered);
    }

    /// Rendering a comment destroys documentation and reporting one is a false
    /// positive -- mios-agents.service documents `${VAR}` in its own header.
    #[test]
    fn comments_are_neither_rendered_nor_reported() {
        let unit = "# ExecStart uses plain ${MIOS_GONE}.\n; also ${MIOS_GONE}\nListenStream=${MIOS_PORT}\n";
        let out = render_file(unit, &cfg(), &ssot(&[("MIOS_PORT", "8320")]));
        assert!(
            out.rendered
                .starts_with("# ExecStart uses plain ${MIOS_GONE}."),
            "{}",
            out.rendered
        );
        assert!(
            out.rendered.contains("; also ${MIOS_GONE}"),
            "{}",
            out.rendered
        );
        assert!(
            out.unresolved.is_empty(),
            "a comment must not be reported: {:?}",
            out.unresolved
        );
    }

    /// Image= is not an Exec line and Quadlet does not expand it, so a floated
    /// tag that resolves to nothing must be NAMED. Four shipped .container
    /// files carried exactly this (task_977).
    #[test]
    fn an_unresolvable_image_tag_is_reported_with_its_line() {
        let unit = "[Container]\nImage=quay.io/ceph/ceph:${MIOS_VERSIONS_CEPH}\n";
        let out = render_file(unit, &cfg(), &ssot(&[]));
        assert_eq!(1, out.unresolved.len(), "{:?}", out.unresolved);
        assert_eq!(2, out.unresolved[0].0);
        assert_eq!("MIOS_VERSIONS_CEPH", out.unresolved[0].1);
    }

    #[test]
    fn an_environment_file_also_confers_runtime_ownership() {
        let unit =
            "[Service]\nEnvironmentFile=-/etc/mios/install.env\nExecStart=/bin/true ${MIOS_X}\n";
        let out = render_file(unit, &cfg(), &ssot(&[("MIOS_X", "9")]));
        assert!(!out.changed, "{}", out.rendered);
    }

    fn vars(pairs: &[(&str, &str)]) -> Vec<(String, String)> {
        pairs
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect()
    }

    /// The env layer used to live inside the expander, reading std::env::var
    /// directly. Moving it to the caller is only safe if it still outranks the
    /// SSOT, exercised here through the real predicate rather than simulated.
    #[test]
    fn a_non_empty_env_var_outranks_the_ssot() {
        let mut map = ssot(&[("MIOS_PORT_FIXTURE_ONLY", "1111")]);
        overlay_from(&mut map, vars(&[("MIOS_PORT_FIXTURE_ONLY", "2222")]));
        let out = render_file("ListenStream=${MIOS_PORT_FIXTURE_ONLY}\n", &cfg(), &map);
        assert!(out.rendered.contains("2222"), "{}", out.rendered);
    }

    /// Empty and unset are the same thing here, which is why the guard exists.
    #[test]
    fn an_empty_env_var_does_not_shadow_the_ssot() {
        let mut map = ssot(&[("MIOS_PORT_FIXTURE_ONLY", "1111")]);
        overlay_from(&mut map, vars(&[("MIOS_PORT_FIXTURE_ONLY", "")]));
        assert_eq!(Some(&"1111".to_string()), map.get("MIOS_PORT_FIXTURE_ONLY"));
    }

    /// A non-MIOS_ export is not ours and must never enter the map.
    #[test]
    fn a_non_mios_env_var_is_not_overlaid() {
        let mut map = ssot(&[]);
        overlay_from(&mut map, vars(&[("PATH", "/nope"), ("WORKER_MODEL", "x")]));
        assert!(map.is_empty(), "{map:?}");
    }

    #[test]
    fn a_config_missing_a_key_cannot_run() {
        let d = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(d.path().join("usr/share/mios")).unwrap();
        std::fs::write(
            d.path().join(SSOT),
            "[build.quadlet_render]\nextensions = [\"toml\"]\nmax_depth = 2\n",
        )
        .unwrap();
        let e = load_config(d.path()).expect_err("must refuse");
        assert!(e.contains("dirs"), "{e}");
    }

    #[test]
    fn an_empty_list_cannot_run_rather_than_rendering_nothing() {
        let d = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(d.path().join("usr/share/mios")).unwrap();
        std::fs::write(
            d.path().join(SSOT),
            "[build.quadlet_render]\ndirs = []\nextensions = [\"toml\"]\nmax_depth = 2\nruntime_ref_directives = [\"ExecStart\"]\n",
        )
        .unwrap();
        let e = load_config(d.path()).expect_err("must refuse");
        assert!(e.contains("empty"), "{e}");
    }

    #[test]
    fn a_malformed_ssot_cannot_run() {
        let d = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(d.path().join("usr/share/mios")).unwrap();
        std::fs::write(d.path().join(SSOT), "[[[not toml").unwrap();
        let e = load_config(d.path()).expect_err("must refuse");
        assert!(e.contains("did not parse"), "{e}");
    }

    /// The shape of every shipped .container that declares env: podman's
    /// --env, which systemd never expands Exec lines against. Under the old
    /// file-wide scan this returned true.
    #[test]
    fn container_environment_does_not_confer_runtime_ownership() {
        let unit = "[Unit]\nDescription=x\n\n[Container]\nImage=example\nEnvironment=FOO=bar\n\n[Install]\nWantedBy=default.target\n";
        assert!(!declares_unit_environment(unit));
    }

    #[test]
    fn service_environment_does_confer_runtime_ownership() {
        let unit = "[Unit]\nDescription=x\n\n[Service]\nEnvironment=FOO=bar\nExecStart=/bin/true\n";
        assert!(declares_unit_environment(unit));
        let file = "[Unit]\nDescription=x\n\n[Service]\nEnvironmentFile=-/etc/mios/install.env\nExecStart=/bin/true\n";
        assert!(declares_unit_environment(file));
    }

    /// mios-pgvector.container's shape: env in BOTH sections, ExecStartPost in
    /// [Service]. Scoping must not cost it the protection it has earned.
    #[test]
    fn service_environment_still_counts_when_container_also_declares_one() {
        let unit = "[Container]\nEnvironment=IN_CONTAINER=1\n\n[Service]\nEnvironment=FOO=bar\nExecStartPost=/bin/true\n";
        assert!(declares_unit_environment(unit));
    }

    /// A [Service] that declares nothing must not inherit ownership from a
    /// [Container] block that appears BEFORE it -- the section state has to be
    /// reset on every header, not latched on the first match.
    #[test]
    fn a_later_service_section_does_not_inherit_an_earlier_container_env() {
        let unit = "[Container]\nEnvironment=FOO=bar\n\n[Service]\nExecStart=/bin/true\n";
        assert!(!declares_unit_environment(unit));
    }

    #[test]
    fn a_surviving_default_form_is_reported_and_escapes_are_not() {
        let text = "# ${DOC:-x} is prose\nEnvironment=A=fedora-${FEDORA_VERSION:-44}\nExecStart=/bin/sh -c 'echo $${HOME:-/}'\nEnvironment=B=${MIOS_X}\n";
        assert_eq!(
            surviving_defaults(text),
            vec![(2, "FEDORA_VERSION".to_string())]
        );
    }
}
