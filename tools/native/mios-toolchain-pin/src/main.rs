// AI-hint: Projects the repo-root rust-toolchain.toml from [build.toolchain], so local and CI lint with the same compiler.
// AI-related: usr/share/mios/mios.toml, automation/98-drift-checks.sh, tools/sync-generated.sh

#![forbid(unsafe_code)]
#![warn(clippy::unwrap_used, clippy::expect_used, clippy::panic)]

use std::path::{Path, PathBuf};
use std::process::ExitCode;

const SSOT: &str = "usr/share/mios/mios.toml";
const OUTPUT: &str = "rust-toolchain.toml";

const USAGE: &str = "usage: mios-toolchain-pin [--root DIR] [--check]\n";

fn die(msg: &str) -> ExitCode {
    eprintln!("mios-toolchain-pin: {msg}");
    ExitCode::from(2)
}

/// One file at the root: rustup ascends, so both workspaces resolve to it.
/// Two would be one value written twice (task_996).
fn render(channel: &str, components: &[String]) -> String {
    let listed = components
        .iter()
        .map(|c| format!("\"{c}\""))
        .collect::<Vec<_>>()
        .join(", ");
    format!(
        "# AI-hint: Pins the Rust toolchain both workspaces build and lint with; \
         GENERATED from [build.toolchain], do not edit.\n\
         # AI-related: {related}\n\
         [toolchain]\n\
         channel = \"{channel}\"\n\
         components = [{listed}]\n",
        related = "usr/share/mios/mios.toml, tools/native/mios-toolchain-pin/src/main.rs"
    )
}

#[derive(Debug)]
struct Pin {
    channel: String,
    components: Vec<String>,
}

fn read_pin(root: &Path) -> Result<Pin, String> {
    let path = root.join(SSOT);
    let text = std::fs::read_to_string(&path)
        .map_err(|e| format!("{SSOT} could not be read ({e}) -- nothing was projected"))?;
    let doc: toml::Value = text
        .parse()
        .map_err(|e| format!("{SSOT} did not parse ({e}) -- nothing was projected"))?;
    let tc = doc
        .get("build")
        .and_then(|b| b.get("toolchain"))
        .ok_or_else(|| format!("{SSOT} has no [build.toolchain] -- nothing was projected"))?;

    let channel = tc
        .get("channel")
        .and_then(|c| c.as_str())
        .ok_or_else(|| "[build.toolchain].channel is missing or not a string".to_string())?
        .trim()
        .to_string();
    // An empty channel would render a toolchain file rustup rejects at every
    // cargo invocation, which is a worse failure than not generating at all.
    if channel.is_empty() {
        return Err(
            "[build.toolchain].channel is empty -- refusing to render a pin rustup cannot resolve"
                .into(),
        );
    }

    let raw = tc
        .get("components")
        .and_then(|c| c.as_array())
        .ok_or_else(|| "[build.toolchain].components is missing or not an array".to_string())?;
    let mut components = Vec::with_capacity(raw.len());
    for v in raw {
        let s = v
            .as_str()
            .ok_or_else(|| "[build.toolchain].components holds a non-string entry".to_string())?;
        if s.trim().is_empty() {
            return Err("[build.toolchain].components holds an empty entry".into());
        }
        components.push(s.trim().to_string());
    }
    // The components ARE the point: CI's `-D warnings` is meaningless if clippy
    // is absent, and an empty list would silently restore the floating state
    // this generator exists to end.
    if components.is_empty() {
        return Err("[build.toolchain].components is empty -- clippy and rustfmt must be declared or the pin buys nothing".into());
    }

    Ok(Pin {
        channel,
        components,
    })
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

    let pin = match read_pin(&root) {
        Ok(p) => p,
        Err(e) => return die(&e),
    };
    let want = render(&pin.channel, &pin.components);
    let out = root.join(OUTPUT);

    if check {
        let have = match std::fs::read_to_string(&out) {
            Ok(s) => s,
            Err(e) => {
                eprintln!(
                    "mios-toolchain-pin: {OUTPUT} is missing or unreadable ({e}) -- \
                     both workspaces are lint-floating; regenerate it"
                );
                return ExitCode::from(1);
            }
        };
        if have == want {
            println!(
                "mios-toolchain-pin: OK: {OUTPUT} matches [build.toolchain] \
                 (channel {}, {} component(s))",
                pin.channel,
                pin.components.len()
            );
            return ExitCode::SUCCESS;
        }
        eprintln!(
            "mios-toolchain-pin: {OUTPUT} differs from its projection -- \
             it was hand-edited, or the SSOT moved and it was not regenerated"
        );
        return ExitCode::from(1);
    }

    if let Err(e) = std::fs::write(&out, &want) {
        return die(&format!("{OUTPUT} could not be written ({e})"));
    }
    println!(
        "mios-toolchain-pin: projected {OUTPUT} (channel {}, {} component(s))",
        pin.channel,
        pin.components.len()
    );
    ExitCode::SUCCESS
}

#[cfg(test)]
// Fixture setup panics on failure by design; the crate-level bans exist
// to keep the production paths from doing that.
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;
    use std::fs;

    fn root_with(ssot: &str) -> tempfile::TempDir {
        let d = tempfile::tempdir().expect("tempdir");
        fs::create_dir_all(d.path().join("usr/share/mios")).expect("mkdir");
        fs::write(d.path().join(SSOT), ssot).expect("write ssot");
        d
    }

    const GOOD: &str =
        "[build.toolchain]\nchannel = \"1.98.0\"\ncomponents = [\"clippy\", \"rustfmt\"]\n";

    #[test]
    fn render_carries_the_channel_and_every_component() {
        let out = render("1.98.0", &["clippy".into(), "rustfmt".into()]);
        assert!(out.contains("channel = \"1.98.0\""), "{out}");
        assert!(
            out.contains("components = [\"clippy\", \"rustfmt\"]"),
            "{out}"
        );
    }

    /// The header is not decoration: check_hint_coverage and
    /// check_template_conformance both read it, and a generated .toml without
    /// one is a drift violation the moment it lands.
    #[test]
    fn render_emits_the_ai_hint_header_its_template_requires() {
        let out = render("1.98.0", &["clippy".into()]);
        assert!(out.starts_with("# AI-hint:"), "{out}");
        assert!(out.contains("# AI-related:"), "{out}");
    }

    #[test]
    fn render_is_parseable_toml_declaring_a_toolchain() {
        let out = render("1.98.0", &["clippy".into(), "rustfmt".into()]);
        let v: toml::Value = out.parse().expect("generated file must be valid TOML");
        let tc = v.get("toolchain").expect("a [toolchain] table");
        assert_eq!(
            "1.98.0",
            tc.get("channel").and_then(|c| c.as_str()).unwrap_or("")
        );
        let comps = tc
            .get("components")
            .and_then(|c| c.as_array())
            .expect("components");
        assert_eq!(2, comps.len());
    }

    #[test]
    fn a_good_ssot_reads_back_exactly() {
        let d = root_with(GOOD);
        let p = read_pin(d.path()).expect("must read");
        assert_eq!("1.98.0", p.channel);
        assert_eq!(
            vec!["clippy".to_string(), "rustfmt".to_string()],
            p.components
        );
    }

    /// An empty channel renders a file rustup rejects at every cargo
    /// invocation, which is worse than not generating at all.
    #[test]
    fn an_empty_channel_is_refused() {
        let d = root_with("[build.toolchain]\nchannel = \"\"\ncomponents = [\"clippy\"]\n");
        let e = read_pin(d.path()).expect_err("must refuse");
        assert!(e.contains("channel is empty"), "{e}");
    }

    /// An empty component list restores the floating state this tool exists to
    /// end: `-D warnings` means nothing if clippy is not installed.
    #[test]
    fn empty_components_are_refused() {
        let d = root_with("[build.toolchain]\nchannel = \"1.98.0\"\ncomponents = []\n");
        let e = read_pin(d.path()).expect_err("must refuse");
        assert!(e.contains("components is empty"), "{e}");
    }

    #[test]
    fn a_non_string_component_is_refused() {
        let d = root_with("[build.toolchain]\nchannel = \"1.98.0\"\ncomponents = [7]\n");
        let e = read_pin(d.path()).expect_err("must refuse");
        assert!(e.contains("non-string"), "{e}");
    }

    /// Absent is not clean. A missing section must be cannot-run, or the check
    /// built on it would report a pass having read nothing.
    #[test]
    fn a_missing_section_cannot_run_rather_than_passing() {
        let d = root_with("[other]\nx = 1\n");
        let e = read_pin(d.path()).expect_err("must refuse");
        assert!(e.contains("no [build.toolchain]"), "{e}");
    }

    #[test]
    fn a_malformed_ssot_cannot_run() {
        let d = root_with("[[[not toml");
        let e = read_pin(d.path()).expect_err("must refuse");
        assert!(e.contains("did not parse"), "{e}");
    }

    #[test]
    fn an_absent_ssot_cannot_run() {
        let d = tempfile::tempdir().expect("tempdir");
        let e = read_pin(d.path()).expect_err("must refuse");
        assert!(e.contains("could not be read"), "{e}");
    }

    /// Round trip: what read_pin yields must render to what a --check of the
    /// same tree compares against, or the generator and its gate disagree.
    #[test]
    fn read_then_render_round_trips() {
        let d = root_with(GOOD);
        let p = read_pin(d.path()).expect("must read");
        let out = render(&p.channel, &p.components);
        let reread: toml::Value = out.parse().expect("valid TOML");
        assert_eq!(
            "1.98.0",
            reread["toolchain"]["channel"].as_str().unwrap_or("")
        );
    }
}
