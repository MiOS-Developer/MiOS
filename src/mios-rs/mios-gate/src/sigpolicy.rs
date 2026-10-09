// AI-hint: Asserts usr/lib/containers/policy.json is byte-identical to what [security.sigstore] projects, the regenerate-and-diff half of Law 8 that this surface never had.
// AI-related: usr/share/mios/mios.toml, usr/lib/containers/policy.json, tools/native/mios-gen/src/main.rs, automation/49-cosign-policy.sh

use crate::Report;
use std::path::Path;

const CHECK: &str = "signature-policy";
const ARTIFACT: &str = "usr/lib/containers/policy.json";

fn cannot_run(why: impl Into<String>) -> Report {
    Report {
        check: CHECK.to_string(),
        ok: false,
        could_not_run: Some(why.into()),
        summary: String::new(),
        findings: Vec::new(),
    }
}

/// Reproduce `tools/generate-cosign-policy.py`'s output exactly.
///
/// That generator emits `json.dumps(policy, indent=2) + "\n"`, which for this
/// shape is four levels of two-space indent. Written out rather than pulled
/// through a JSON library so the expected BYTES are visible here: the defect
/// this check exists to catch was a generator whose `--check` compared parsed
/// JSON and so could not see that the tracked file was compact while the
/// writer emitted indented.
fn render(policy_mode: &str) -> String {
    format!("{{\n  \"default\": [\n    {{\n      \"type\": \"{policy_mode}\"\n    }}\n  ]\n}}\n")
}

pub fn check(root: &Path) -> Report {
    let ssot = root.join("usr/share/mios/mios.toml");
    let artifact = root.join(ARTIFACT);
    if !ssot.is_file() {
        return cannot_run(format!("{} is missing", ssot.display()));
    }
    let Ok(ssot_text) = std::fs::read_to_string(&ssot) else {
        return cannot_run("usr/share/mios/mios.toml could not be read");
    };
    let Ok(ssot_val) = ssot_text.parse::<toml::Value>() else {
        return cannot_run("usr/share/mios/mios.toml did not parse");
    };

    // Absent is a violation, not a skip: the artifact is tracked, and a policy
    // that is not there is not a permissive policy -- it is an unknown one.
    if !artifact.is_file() {
        return Report {
            check: CHECK.to_string(),
            ok: false,
            could_not_run: None,
            summary: String::new(),
            findings: vec![format!(
                "{ARTIFACT} is tracked but missing -- regenerate it: \
                 python3 tools/generate-cosign-policy.py"
            )],
        };
    }

    let sigstore = ssot_val
        .get("security")
        .and_then(|s| s.get("sigstore"))
        .and_then(|s| s.as_table());
    let Some(sigstore) = sigstore else {
        return cannot_run("mios.toml declares no [security.sigstore] table");
    };
    let Some(policy_mode) = sigstore.get("policy_mode").and_then(|v| v.as_str()) else {
        return cannot_run("[security.sigstore].policy_mode is absent or not a string");
    };
    if policy_mode.is_empty() {
        return cannot_run("[security.sigstore].policy_mode is empty");
    }

    let Ok(current) = std::fs::read_to_string(&artifact) else {
        return cannot_run(format!("{ARTIFACT} could not be read"));
    };
    let expected = render(policy_mode);

    let mut findings = Vec::new();
    if current != expected {
        findings.push(format!(
            "{ARTIFACT} is not what [security.sigstore].policy_mode = \"{policy_mode}\" \
             projects -- regenerate it: python3 tools/generate-cosign-policy.py"
        ));
    }

    // Report the mode in the summary rather than judging it. Whether
    // insecureAcceptEverything is the right value is task_973's question; this
    // check's job is that the file and the SSOT agree.
    let ok = findings.is_empty();
    Report {
        check: CHECK.to_string(),
        ok,
        could_not_run: None,
        summary: format!("{ARTIFACT} matches [security.sigstore].policy_mode = \"{policy_mode}\""),
        findings,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_render_matches_the_python_generators_bytes() {
        // Exactly json.dumps({"default":[{"type":M}]}, indent=2) + "\n".
        assert_eq!(
            render("insecureAcceptEverything"),
            "{\n  \"default\": [\n    {\n      \"type\": \"insecureAcceptEverything\"\n    }\n  ]\n}\n"
        );
    }

    #[test]
    fn test_render_tracks_the_mode() {
        assert!(render("reject").contains("\"type\": \"reject\""));
        assert_ne!(render("reject"), render("insecureAcceptEverything"));
    }
}
