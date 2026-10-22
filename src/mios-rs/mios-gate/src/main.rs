// AI-hint: Entry point for mios-gate, the native drift-gate binary; dispatches one named check and reports text or an OpenAI-format structured object.
// AI-related: src/mios-rs/mios-gate/src/dispatch.rs, automation/98-drift-checks.sh, usr/share/doc/mios/adr/0021-rust-static-binary-consolidation.md

#![forbid(unsafe_code)]
#![warn(clippy::unwrap_used, clippy::expect_used, clippy::panic)]

mod artifact;
mod artifact_layers;
mod artifact_recipes;
mod canonical_bools;
mod credentials;
mod dispatch;
mod doc_refs;
mod gate_registry;
mod host_parity;
mod image_equivalence;
mod image_freshness;
mod inert_tables;
mod laws;
mod module_tests;
mod negative_coverage;
mod phases;
mod powershell;
mod profiles;
mod projreg;
mod protected_refs;
mod ratchet;
mod rendercov;
mod rust_categories;
mod shell_lint;
mod sigpolicy;
mod static_linkage;
mod stubs;
mod version_literals;

use std::process::ExitCode;

/// Exit codes are the gate's contract and predate this binary: 0 clean,
/// 1 violations found, 2 the check could not run. Never 0 on an unread input.
pub const EXIT_CLEAN: u8 = 0;
pub const EXIT_VIOLATIONS: u8 = 1;
pub const EXIT_CANNOT_RUN: u8 = 2;

/// One check's result. `findings` is empty exactly when `ok` is true.
pub struct Report {
    pub check: String,
    pub ok: bool,
    pub could_not_run: Option<String>,
    pub summary: String,
    pub findings: Vec<String>,
}

impl Report {
    pub fn code(&self) -> u8 {
        if self.could_not_run.is_some() {
            EXIT_CANNOT_RUN
        } else if self.ok {
            EXIT_CLEAN
        } else {
            EXIT_VIOLATIONS
        }
    }

    fn render_text(&self) -> String {
        let mut out = String::new();
        if let Some(why) = &self.could_not_run {
            out.push_str(&format!("    [{}] {}\n", self.check, why));
            return out;
        }
        for f in &self.findings {
            out.push_str(&format!("    [{}] {}\n", self.check, f));
        }
        if self.ok {
            out.push_str(&format!("[{}] {}\n", self.check, self.summary));
        }
        out
    }

    /// OpenAI-format structured output: the object a `/v1` structured-outputs
    /// response would carry, so the agent plane consumes findings natively.
    fn render_json(&self) -> String {
        let value = serde_json::json!({
            "check": self.check,
            "status": if self.could_not_run.is_some() { "could_not_run" }
                      else if self.ok { "clean" } else { "violations" },
            "summary": self.could_not_run.clone().unwrap_or_else(|| self.summary.clone()),
            "findings": self.findings,
        });
        serde_json::to_string_pretty(&value).unwrap_or_else(|_| {
            // Serialising our own object cannot fail, but a gate never panics.
            format!(
                "{{\"check\":\"{}\",\"status\":\"could_not_run\",\
                     \"summary\":\"report could not be serialised\",\"findings\":[]}}",
                self.check
            )
        })
    }
}

const USAGE: &str = "usage: mios-gate <check> [--root DIR] [--format text|json]\n\
                     \x20      mios-gate image-equivalence --root DIR --profile P [--ssot FILE] [--allow-tree-only]\n\
                     \x20      mios-gate static-linkage [--root DIR] [--format text|json] [--binary PATH] [--arch ARCH] [--census]\n\
                     checks: artifact, artifact-recipes, build-tool-dispatch, canonical-bools, credential-literals,\n\
                             doc-refs-headers, doc-refs-resolve, drift-stubs, gate-registry, generator-host-parity,\n\
                             image-equivalence, image-freshness, module-test-coverage, native-policy, negative-coverage,\n\
                             no-inert-ssot-tables, phase-ratchet, phase-registry, profile-integrity,\n\
                             powershell-parse, powershell-analyze, projection-coverage, protected-refs,\n\
                             ratchet-direction, render-coverage, root-fhs-overlay, rust-categories, shell-lint,\n\
                             signature-policy, static-linkage, version-literals-ssot\n";

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let mut check: Option<String> = None;
    let mut root: Option<String> = std::env::var("MIOS_DRIFT_ROOT").ok();
    let mut json = false;
    // image-equivalence only: what it asserts against, and for which profile.
    let mut ssot: Option<String> = None;
    let mut profile: Option<String> = None;
    let mut allow_tree_only = false;
    // static-linkage only: optional single binary and architecture override,
    // and the per-binary census (SHA-256, interpreter, DT_NEEDED) as JSON.
    let mut binary: Option<String> = None;
    let mut arch: Option<String> = None;
    let mut census = false;

    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--root" => {
                i += 1;
                match args.get(i) {
                    Some(v) => root = Some(v.clone()),
                    None => {
                        eprint!("mios-gate: --root needs a directory\n{USAGE}");
                        return ExitCode::from(EXIT_CANNOT_RUN);
                    }
                }
            }
            "--format" => {
                i += 1;
                match args.get(i).map(String::as_str) {
                    Some("json") => json = true,
                    Some("text") => json = false,
                    _ => {
                        eprint!("mios-gate: --format takes text or json\n{USAGE}");
                        return ExitCode::from(EXIT_CANNOT_RUN);
                    }
                }
            }
            "--ssot" | "--profile" => {
                let flag = args[i].clone();
                i += 1;
                let Some(v) = args.get(i).cloned() else {
                    eprint!("mios-gate: {flag} needs a value\n{USAGE}");
                    return ExitCode::from(EXIT_CANNOT_RUN);
                };
                if flag == "--ssot" {
                    ssot = Some(v);
                } else {
                    profile = Some(v);
                }
            }
            "--binary" => {
                i += 1;
                let Some(v) = args.get(i).cloned() else {
                    eprint!("mios-gate: --binary needs a path\n{USAGE}");
                    return ExitCode::from(EXIT_CANNOT_RUN);
                };
                binary = Some(v);
            }
            "--arch" => {
                i += 1;
                let Some(v) = args.get(i).cloned() else {
                    eprint!("mios-gate: --arch needs a value\n{USAGE}");
                    return ExitCode::from(EXIT_CANNOT_RUN);
                };
                arch = Some(v);
            }
            "--allow-tree-only" => allow_tree_only = true,
            "--census" => census = true,
            "-h" | "--help" => {
                print!("{USAGE}");
                return ExitCode::from(EXIT_CLEAN);
            }
            other if check.is_none() && !other.starts_with('-') => {
                check = Some(other.to_string());
            }
            other => {
                eprint!("mios-gate: unrecognised argument {other:?}\n{USAGE}");
                return ExitCode::from(EXIT_CANNOT_RUN);
            }
        }
        i += 1;
    }

    let Some(name) = check else {
        eprint!("mios-gate: no check named\n{USAGE}");
        return ExitCode::from(EXIT_CANNOT_RUN);
    };
    let root = std::path::PathBuf::from(root.unwrap_or_else(|| {
        if !std::path::Path::new("usr/share/mios/mios.toml").is_file()
            && std::path::Path::new("/usr/share/mios/mios.toml").is_file()
        {
            "/".to_string()
        } else {
            ".".to_string()
        }
    }));
    if name != "image-equivalence" && (ssot.is_some() || profile.is_some() || allow_tree_only) {
        eprint!("mios-gate: --ssot, --profile and --allow-tree-only belong to image-equivalence\n{USAGE}");
        return ExitCode::from(EXIT_CANNOT_RUN);
    }
    if name != "static-linkage" && name != "native-policy" && (binary.is_some() || arch.is_some() || census) {
        eprint!("mios-gate: --binary, --arch and --census belong to static-linkage\n{USAGE}");
        return ExitCode::from(EXIT_CANNOT_RUN);
    }
    if census {
        let (text, code) = static_linkage::census(&static_linkage::Options {
            root: root.clone(),
            binary: binary.map(std::path::PathBuf::from),
            arch,
        });
        println!("{text}");
        return ExitCode::from(code);
    }

    let report = match name.as_str() {
        "artifact" => artifact::check(&root),
        "artifact-recipes" => artifact_recipes::check(&root),
        "build-tool-dispatch" => dispatch::check(&root),
        "canonical-bools" => canonical_bools::check(&root),
        "credential-literals" => credentials::check(&root),
        "doc-refs-headers" => doc_refs::headers(&root),
        "doc-refs-resolve" => doc_refs::check(&root),
        "drift-stubs" => stubs::check(&root),
        "gate-registry" => gate_registry::check(&root),
        "generator-host-parity" => host_parity::check(&root),
        "image-equivalence" => image_equivalence::check(&image_equivalence::Options {
            root: root.clone(),
            ssot: ssot.map(std::path::PathBuf::from),
            profile,
            allow_tree_only,
        }),
        "image-freshness" => image_freshness::check(&root),
        "law-enforcers" => laws::check(&root),
        "module-test-coverage" => module_tests::check(&root),
        "negative-coverage" => negative_coverage::check(&root),
        "no-inert-ssot-tables" => inert_tables::check(&root),
        "phase-ratchet" => phases::ratchet(&root),
        "phase-registry" => phases::check(&root),
        "powershell-parse" => powershell::check(&root, false),
        "powershell-analyze" => powershell::check(&root, true),
        "profile-integrity" => profiles::check(&root),
        "projection-coverage" => projreg::check(&root),
        "protected-refs" => protected_refs::check(&root),
        "ratchet-direction" => ratchet::check(&root),
        "render-coverage" => rendercov::check(&root),
        "root-fhs-overlay" | "fhs-root-overlay" => host_parity::check_fhs_overlay(&root),
        "rust-categories" => rust_categories::check(&root),
        "shell-lint" => shell_lint::check(&root),
        "signature-policy" => sigpolicy::check(&root),
        "static-linkage" | "native-policy" => static_linkage::check(&static_linkage::Options {
            root: root.clone(),
            binary: binary.map(std::path::PathBuf::from),
            arch,
        }),
        "version-literals-ssot" => version_literals::check(&root),
        _ => {
            eprint!("mios-gate: no such check {name:?}\n{USAGE}");
            return ExitCode::from(EXIT_CANNOT_RUN);
        }
    };

    if json {
        println!("{}", report.render_json());
    } else {
        let text = report.render_text();
        if report.ok && report.could_not_run.is_none() {
            print!("{text}");
        } else {
            eprint!("{text}");
        }
    }
    ExitCode::from(report.code())
}
