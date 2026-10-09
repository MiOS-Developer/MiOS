// AI-hint: Asserts every miosd drift Check that was never implemented is on the shrink-only register in SSOT, and that no stub claims a verdict.
// AI-related: src/mios-rs/miosd/src/drift/, usr/share/mios/mios.toml, Containerfile, tasks.jsonl

use crate::Report;
use std::collections::BTreeSet;
use std::path::Path;

const CHECK: &str = "drift-stubs";

/// The marker a stub's Skip message must carry. A stub that says anything else
/// is a stub pretending to have run.
const MARKER: &str = "NOT IMPLEMENTED:";

/// Whether a `run` body actually consults the tree. Naming the parameter `ctx`
/// instead of `_ctx` proves nothing: check_pipeline_numbering read `ctx.in_image`
/// for an early skip and then returned a constant Pass, so a parameter-name test
/// classified it as implemented. rustfmt also splits `ctx\n    .root`, so the
/// comparison is made on a whitespace-stripped copy or it misses real readers.
fn reads_the_tree(run_body: &str) -> bool {
    let squashed: String = run_body.chars().filter(|c| !c.is_whitespace()).collect();
    // Delegation counts: everything in projections.rs hands ctx to
    // regen_and_diff, which does the reading. Eleven checks look blind without
    // this and are not.
    // Handing the root itself to a callee as an argument is the same delegation:
    // native_generator::command(&ctx.root, "pod-quadlets", true) runs the
    // generator's --check over that tree. A path built from it is not
    // (`ctx.root.join(` never matches "(ctx.root,"), so task_982 still holds.
    let delegates = squashed.contains("(ctx,")
        || squashed.contains("(&ctx,")
        || squashed.contains("(ctx)")
        || squashed.contains("(&ctx)")
        || squashed.contains("(&ctx.root,")
        || squashed.contains("(ctx.root,");
    // task_982: `ctx.root.join("x")` builds a PATH and `.exists()` asks whether a
    // file is there. Neither opens it. Three checks claimed a verdict after
    // doing exactly that, and two earlier versions of this predicate -- one
    // testing the parameter NAME, one testing whether ctx.root is mentioned at
    // all -- passed them both. Require an actual read.
    let reads = squashed.contains("read_to_string")
        || squashed.contains("read_dir")
        || squashed.contains("fs::read(")
        || squashed.contains(".parse::<")
        || squashed.contains("Command::new");
    delegates || reads
}

/// Extracts the body of `fn run(...)` from an impl block, or None if absent.
fn run_body(block: &str) -> Option<&str> {
    let i = block.find("fn run(&self,")?;
    let rest = &block[i..];
    let open = rest.find('{')?;
    let end = rest[open..].find("\n    }")? + open;
    Some(&rest[open + 1..end])
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

/// Splits a Rust source file into `impl Check for <Struct> { ... }` blocks.
/// Deliberately textual: the point is to notice a stub the moment it is WRITTEN,
/// which is a source fact, not a runtime one.
fn impl_blocks(src: &str) -> Vec<(String, String)> {
    let mut out = Vec::new();
    let mut rest = src;
    while let Some(i) = rest.find("impl Check for ") {
        let after = &rest[i + "impl Check for ".len()..];
        let Some(brace) = after.find('{') else { break };
        let name: String = after[..brace]
            .trim()
            .chars()
            .take_while(|c| c.is_alphanumeric() || *c == '_')
            .collect();
        // The impl ends at the first line that is exactly "}" at column 0.
        let body_start = i + "impl Check for ".len() + brace + 1;
        let tail = &rest[body_start..];
        let end = tail.find("\n}").map(|e| e + 1).unwrap_or(tail.len());
        out.push((name, tail[..end].to_string()));
        rest = &rest[body_start + end..];
    }
    out
}

fn check_id(block: &str) -> Option<String> {
    let i = block.find("fn id(")?;
    let after = &block[i..];
    let q = after.find('"')?;
    let rest = &after[q + 1..];
    let e = rest.find('"')?;
    Some(rest[..e].to_string())
}

pub fn check(root: &Path) -> Report {
    let ssot = root.join("usr/share/mios/mios.toml");
    let dir = root.join("src/mios-rs/miosd/src/drift");
    if !ssot.is_file() {
        return cannot_run(format!("{} is missing", ssot.display()));
    }
    if !dir.is_dir() {
        return cannot_run(format!("{} is missing", dir.display()));
    }
    let Ok(text) = std::fs::read_to_string(&ssot) else {
        return cannot_run("usr/share/mios/mios.toml could not be read");
    };
    let Ok(val) = text.parse::<toml::Value>() else {
        return cannot_run("usr/share/mios/mios.toml did not parse");
    };
    let reg = val.get("drift").and_then(|d| d.get("unimplemented"));
    let Some(ceiling) = reg
        .and_then(|r| r.get("max_unimplemented"))
        .and_then(|v| v.as_integer())
    else {
        return cannot_run("mios.toml [drift.unimplemented].max_unimplemented is absent");
    };
    let listed: BTreeSet<String> = reg
        .and_then(|r| r.get("checks"))
        .and_then(|v| v.as_array())
        .map(|a| {
            a.iter()
                .filter_map(|v| v.as_str())
                .map(|s| s.trim().to_string())
                .collect()
        })
        .unwrap_or_default();

    let Ok(rd) = std::fs::read_dir(&dir) else {
        return cannot_run("the drift module directory could not be read");
    };
    let mut files: Vec<std::path::PathBuf> = rd
        .flatten()
        .map(|e| e.path())
        .filter(|p| p.extension().map(|x| x == "rs").unwrap_or(false))
        .collect();
    files.sort();
    if files.is_empty() {
        return cannot_run("no drift modules were scanned, so nothing was compared");
    }

    let mut stubs: BTreeSet<String> = BTreeSet::new();
    let mut findings: Vec<String> = Vec::new();
    let mut impls = 0usize;
    for path in &files {
        // mod.rs holds the Verdict type and its unit-test doubles, not checks.
        if path.file_name().map(|n| n == "mod.rs").unwrap_or(false) {
            continue;
        }
        let Ok(src) = std::fs::read_to_string(path) else {
            continue;
        };
        for (struct_name, block) in impl_blocks(&src) {
            impls += 1;
            let Some(body) = run_body(&block) else {
                continue;
            };
            if reads_the_tree(body) {
                continue;
            }
            let Some(id) = check_id(&block) else {
                findings.push(format!("{struct_name}: a Check impl with no readable id()"));
                continue;
            };
            // A run that never reads the tree cannot report Pass. That is the
            // whole defect: 54 of these claimed verification at every bake.
            if body.contains("Verdict::Pass(") {
                findings.push(format!(
                    "{id}: run() never consults ctx.root, yet returns Verdict::Pass -- \
                     a check that cannot look must not claim"
                ));
            }
            if body.contains(MARKER) {
                stubs.insert(id);
            }
        }
    }
    if impls == 0 {
        return cannot_run("no `impl Check for` blocks were found, so nothing was compared");
    }

    for id in stubs.difference(&listed) {
        findings.push(format!(
            "{id}: an unimplemented check that is not on [drift.unimplemented].checks -- \
             register it, or implement it"
        ));
    }
    for id in listed.difference(&stubs) {
        findings.push(format!(
            "{id}: on [drift.unimplemented].checks but no longer a stub -- take it off the register"
        ));
    }

    // Shrink-only, and a ceiling above the measurement is slack of its own.
    let measured = stubs.len() as i64;
    if measured > ceiling {
        findings.push(format!(
            "{measured} unimplemented check(s) exceeds the ceiling {ceiling} -- implement one \
             instead of raising max_unimplemented"
        ));
    } else if measured < ceiling {
        findings.push(format!(
            "[drift.unimplemented].max_unimplemented is {ceiling} but only {measured} check(s) are \
             stubs -- lower the ceiling to {measured}"
        ));
    }

    let ok = findings.is_empty();
    Report {
        check: CHECK.to_string(),
        ok,
        could_not_run: None,
        summary: format!(
            "{impls} Check impl(s) scanned, {measured} unimplemented and registered (ceiling {ceiling})"
        ),
        findings,
    }
}
