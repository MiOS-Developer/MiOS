// AI-hint: Asserts every automation/NN-*.sh on disk is registered in mios.toml [build.phases].list, or is named on the shrink-only unregistered register with a reason.
// AI-related: usr/share/mios/mios.toml, automation/build.sh, automation/55-native-build.sh, tasks.jsonl

use crate::Report;
use std::collections::BTreeSet;
use std::path::Path;

const CHECK: &str = "phase-registry";

fn cannot_run(why: impl Into<String>) -> Report {
    Report {
        check: CHECK.to_string(),
        ok: false,
        could_not_run: Some(why.into()),
        summary: String::new(),
        findings: Vec::new(),
    }
}

/// A phase script is `NN-name.sh` directly under automation/.
fn is_phase_name(name: &str) -> bool {
    let b = name.as_bytes();
    b.len() > 3
        && b[0].is_ascii_digit()
        && b[1].is_ascii_digit()
        && b[2] == b'-'
        && name.ends_with(".sh")
}

/// The phase scripts directly under `dir`, or None when it cannot be listed.
fn phase_scripts(dir: &Path) -> Option<BTreeSet<String>> {
    let rd = std::fs::read_dir(dir).ok()?;
    Some(
        rd.flatten()
            .filter(|e| e.path().is_file())
            .map(|e| e.file_name().to_string_lossy().to_string())
            .filter(|n| is_phase_name(n))
            .collect(),
    )
}

/// The phase-script count must equal `[build.ratchet].max_phase_scripts`:
/// above it is growth, below it is slack the next phase hides in, and an
/// absent ceiling cannot run (the bash check defaulted it to 71).
pub fn ratchet(root: &Path) -> Report {
    let report = |ok: bool, cnr: Option<String>, summary: String, findings: Vec<String>| Report {
        check: "phase-ratchet".to_string(),
        ok,
        could_not_run: cnr,
        summary,
        findings,
    };
    let ceiling = std::fs::read_to_string(root.join("usr/share/mios/mios.toml"))
        .ok()
        .and_then(|t| t.parse::<toml::Value>().ok())
        .and_then(|v| {
            v.get("build")?
                .get("ratchet")?
                .get("max_phase_scripts")?
                .as_integer()
        });
    let Some(ceiling) = ceiling else {
        let why =
            "mios.toml [build.ratchet].max_phase_scripts is absent, unreadable or not a count";
        return report(false, Some(why.into()), String::new(), Vec::new());
    };
    let measured = match phase_scripts(&root.join("automation")) {
        Some(s) if !s.is_empty() => s.len() as i64,
        _ => {
            let why = "automation/ holds no NN-*.sh phase script, so nothing was counted";
            return report(false, Some(why.into()), String::new(), Vec::new());
        }
    };
    let mut findings = Vec::new();
    if measured > ceiling {
        findings.push(format!(
            "{measured} phase script(s) exceed [build.ratchet].max_phase_scripts = {ceiling} -- \
             fold a phase instead of raising the ceiling"
        ));
    } else if measured < ceiling {
        findings.push(format!(
            "[build.ratchet].max_phase_scripts is {ceiling} but only {measured} phase script(s) \
             exist -- lower the ceiling to {measured}"
        ));
    }
    let summary = format!("{measured} phase script(s) on disk (ceiling {ceiling})");
    report(findings.is_empty(), None, summary, findings)
}

pub fn check(root: &Path) -> Report {
    let ssot = root.join("usr/share/mios/mios.toml");
    let dir = root.join("automation");
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
    let phases = val.get("build").and_then(|b| b.get("phases"));

    // An absent or empty list is unbounded debt, not zero debt: build.sh then
    // falls back to a hardcoded registry and silently runs a fraction of the
    // pipeline. Never report that as clean.
    let Some(entries) = phases
        .and_then(|p| p.get("list"))
        .and_then(|v| v.as_array())
        .filter(|a| !a.is_empty())
    else {
        return cannot_run("mios.toml [build.phases].list is absent or empty");
    };
    let Some(ceiling) = phases
        .and_then(|p| p.get("max_unregistered"))
        .and_then(|v| v.as_integer())
    else {
        return cannot_run("mios.toml [build.phases].max_unregistered is absent");
    };
    let registered_exceptions: BTreeSet<String> = phases
        .and_then(|p| p.get("unregistered"))
        .and_then(|v| v.as_array())
        .map(|a| {
            a.iter()
                .filter_map(|v| v.as_str())
                .map(|s| s.trim().to_string())
                .collect()
        })
        .unwrap_or_default();

    let mut listed: BTreeSet<String> = BTreeSet::new();
    let mut findings: Vec<String> = Vec::new();
    for e in entries {
        let Some(script) = e.get("script").and_then(|v| v.as_str()) else {
            findings.push("a [build.phases].list entry has no `script` key".to_string());
            continue;
        };
        // An ordinal that disagrees with its own filename is how a phase gets
        // reordered without anyone reading the diff.
        if let Some(ord) = e.get("ordinal").and_then(|v| v.as_str()) {
            if !script.starts_with(&format!("{ord}-")) {
                findings.push(format!(
                    "{script}: ordinal {ord:?} does not match the script's own numeric prefix"
                ));
            }
        }
        listed.insert(script.to_string());
    }

    let Some(on_disk) = phase_scripts(&dir) else {
        return cannot_run("automation/ could not be read");
    };
    if on_disk.is_empty() {
        return cannot_run("automation/ holds no NN-*.sh phase scripts, so nothing was compared");
    }

    // A listed script that does not exist is dropped by build.sh with no else
    // branch: the pipeline runs one stage short and exits 0. Never excusable.
    for script in listed.difference(&on_disk) {
        findings.push(format!(
            "{script}: registered in [build.phases].list but absent from automation/"
        ));
    }

    let unregistered: Vec<String> = on_disk.difference(&listed).cloned().collect();
    for script in &unregistered {
        if !registered_exceptions.contains(script) {
            findings.push(format!(
                "{script}: on disk but NOT in [build.phases].list, so build.sh never runs it -- \
                 register the phase, or name it in [build.phases].unregistered with a reason"
            ));
        }
    }
    for script in &registered_exceptions {
        if !unregistered.contains(script) {
            findings.push(format!(
                "{script}: named in [build.phases].unregistered but it is registered (or gone) -- \
                 drop it from the register"
            ));
        }
    }

    // Shrink-only: a ceiling above the measurement is slack the next regression
    // hides in, so it is a violation in its own right.
    let measured = unregistered.len() as i64;
    if measured > ceiling {
        findings.push(format!(
            "{measured} unregistered phase script(s) exceeds the register ceiling {ceiling} -- \
             register the phase instead of raising max_unregistered"
        ));
    } else if measured < ceiling {
        findings.push(format!(
            "[build.phases].max_unregistered is {ceiling} but only {measured} script(s) are \
             unregistered -- lower the ceiling to {measured}"
        ));
    }

    let ok = findings.is_empty();
    Report {
        check: CHECK.to_string(),
        ok,
        could_not_run: None,
        summary: format!(
            "{} phase script(s) on disk, {} registered, {} on the shrink-only register (ceiling {})",
            on_disk.len(),
            listed.len(),
            measured,
            ceiling
        ),
        findings,
    }
}
