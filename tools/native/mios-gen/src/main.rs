// AI-hint: Main entrypoint for mios-gen -- the unified SSOT projector and code generator binary (ADR-0021 gen category).
// AI-related: usr/share/mios/mios.toml, tools/native/Cargo.toml, docs/design/doc-rust-static-port.md, usr/share/doc/mios/adr/0021-rust-static-binary-consolidation.md

#![forbid(unsafe_code)]

use clap::{Parser, Subcommand};
use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::ExitCode;

mod adr_index;
mod ai_manifest;
mod bib_configs;
mod btop_theme;
mod cargo_manifests;
mod fastfetch;
mod gate_index;
mod metal_vs_hosted;
mod pipe_boundaries;
mod pipeline_index;
mod pod_quadlets;
mod render_desktop;
mod render_globals;
mod render_manpages;
mod render_ports;
mod roadmap_index;
mod standardize_docs;
mod sync_wiki;
mod tmux_theme;

#[derive(Parser, Debug)]
#[command(
    name = "mios-gen",
    version,
    about = "MiOS SSOT projector verbs (ADR-0021 gen category)"
)]
struct Cli {
    /// Output format (text or json)
    #[arg(long, global = true, default_value = "text")]
    format: String,

    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand, Debug)]
enum Commands {
    /// Renders usr/lib/containers/policy.json from usr/share/mios/mios.toml [security.sigstore] SSOT
    #[command(name = "cosign-policy")]
    CosignPolicy {
        /// Repository root directory
        #[arg(long)]
        root: Option<PathBuf>,

        /// Check mode: verify committed file is in sync with SSOT without modifying it
        #[arg(long)]
        check: bool,
    },

    /// Generates the agent outbound egress nftables ruleset from [security.egress] SSOT
    #[command(name = "egress-firewall")]
    EgressFirewall {
        /// Repository root directory
        #[arg(long)]
        root: Option<PathBuf>,
    },

    /// Generates usr/share/mios/reference/drift-gate-index.tsv from automation/98-drift-checks.sh
    #[command(name = "gate-index")]
    GateIndex {
        /// Repository root directory
        #[arg(long)]
        root: Option<PathBuf>,

        /// Path to 98-drift-checks.sh (default: automation/98-drift-checks.sh)
        #[arg(long)]
        script: Option<PathBuf>,

        /// Output path for drift-gate-index.tsv
        #[arg(long)]
        output: Option<PathBuf>,

        /// Check mode: verify committed TSV is in sync without modifying it
        #[arg(long)]
        check: bool,
    },

    /// Generates usr/share/mios/reference/pipeline-index.tsv from automation/[0-9][0-9]-*.sh and SSOT
    #[command(name = "pipeline-index")]
    PipelineIndex {
        /// Repository root directory
        #[arg(long)]
        root: Option<PathBuf>,

        /// Check mode: verify committed TSV is in sync without modifying it
        #[arg(long)]
        check: bool,
    },

    /// Generates repo-root ADR.md breadcrumb index from usr/share/doc/mios/adr/
    #[command(name = "adr-index")]
    AdrIndex {
        /// Repository root directory
        #[arg(long)]
        root: Option<PathBuf>,

        /// Check mode: verify committed ADR.md is in sync without modifying it
        #[arg(long)]
        check: bool,
    },

    /// Generates usr/share/doc/mios/reference/metal-vs-hosted.md from [blade] SSOT
    #[command(name = "metal-vs-hosted")]
    MetalVsHosted {
        /// Repository root directory
        #[arg(long)]
        root: Option<PathBuf>,

        /// Check mode: verify committed markdown is in sync without modifying it
        #[arg(long)]
        check: bool,
    },

    /// Generates and validates Table of Contents, Index, Metrics, and Rollup in ROADMAP.md
    #[command(name = "roadmap-index")]
    RoadmapIndex {
        /// Repository root directory
        #[arg(long)]
        root: Option<PathBuf>,

        /// Check mode: verify committed ROADMAP.md is in sync without modifying it
        #[arg(long)]
        check: bool,
    },

    /// Generates or checks AI repository and tool manifests from Markdown and source files
    #[command(name = "ai-manifest")]
    AiManifest {
        /// Repository root directory
        #[arg(long)]
        root: Option<PathBuf>,

        /// Check mode: verify committed manifests are in sync without modifying them
        #[arg(long)]
        check: bool,
    },

    /// Derives and checks [ports] flat table and literals from [ports.categories] SSOT
    #[command(name = "render-ports", alias = "ports")]
    RenderPorts {
        /// Repository root directory
        #[arg(long)]
        root: Option<PathBuf>,

        /// Path to mios.toml (default: usr/share/mios/mios.toml under root)
        #[arg(long)]
        toml: Option<PathBuf>,

        /// Check mode: verify flat table and port fallbacks match SSOT without modifying
        #[arg(long)]
        check: bool,

        /// Print mode: print sorted derived ports
        #[arg(long = "print")]
        print_ports: bool,
    },

    /// Renders all .desktop launchers in usr/share/applications/ from [desktop.launchers] SSOT
    #[command(name = "render-desktop", alias = "desktop")]
    RenderDesktop {
        /// Repository root directory
        #[arg(long)]
        root: Option<PathBuf>,

        /// Check mode: verify committed .desktop files are in sync without modifying them
        #[arg(long)]
        check: bool,
    },

    /// Generates automation/lib/globals.sh and globals.ps1 from mios.toml SSOT
    #[command(name = "render-globals", alias = "globals")]
    RenderGlobals {
        /// Repository root directory
        #[arg(long)]
        root: Option<PathBuf>,

        /// Check mode: verify committed globals.sh and globals.ps1 match SSOT without modifying them
        #[arg(long)]
        check: bool,
    },

    /// Renders and validates the native roff manual tree from SSOT [verbs]
    #[command(name = "render-manpages", alias = "manpages")]
    RenderManpages {
        /// Repository root directory
        #[arg(long)]
        root: Option<PathBuf>,

        /// Check mode: verify committed manual pages are in sync without modifying them
        #[arg(long)]
        check: bool,

        /// Validate mode: validate roff structural integrity of all rendered pages
        #[arg(long)]
        validate: bool,
    },

    /// Projects [deploy.artifacts] filesystem sizing from mios.toml SSOT into config/artifacts/*.toml
    #[command(name = "bib-configs", alias = "bib")]
    BibConfigs {
        /// Repository root directory
        #[arg(long)]
        root: Option<PathBuf>,

        /// Check mode: verify committed artifact configs match SSOT without modifying them
        #[arg(long)]
        check: bool,
    },

    /// Projects tools/native/Cargo.toml workspace members and version from SSOT
    #[command(name = "cargo-manifests", alias = "cargo-manifest")]
    CargoManifests {
        /// Repository root directory
        #[arg(long)]
        root: Option<PathBuf>,

        /// Check mode: verify committed tools/native/Cargo.toml matches projection without modifying it
        #[arg(long)]
        check: bool,
    },

    /// Generates machine-readable pipe-boundaries.manifest.json for agent-pipe DI contract
    #[command(
        name = "pipe-boundaries",
        alias = "pipe-boundary-manifest",
        alias = "pipe-manifest"
    )]
    PipeBoundaries {
        /// Repository root directory
        #[arg(long)]
        root: Option<PathBuf>,

        /// Check mode: verify committed pipe-boundaries.manifest.json matches projection without modifying it
        #[arg(long)]
        check: bool,
    },

    /// Standardizes headers and footers across specs/ markdown documentation
    #[command(name = "standardize-docs", alias = "docs-standardize")]
    StandardizeDocs {
        /// Repository root directory
        #[arg(long)]
        root: Option<PathBuf>,

        /// Check mode: verify committed documentation matches standardized format without modifying it
        #[arg(long)]
        check: bool,

        /// Explicit target files or directories to standardize (defaults to specs/ subdirectories)
        #[arg(value_name = "PATHS")]
        paths: Vec<PathBuf>,
    },

    /// Synchronizes version and RAG metadata in wiki and markdown documentation
    #[command(name = "sync-wiki", alias = "wiki-sync")]
    SyncWiki {
        /// Repository root directory
        #[arg(long)]
        root: Option<PathBuf>,

        /// Check mode: verify embedded metadata matches SSOT without modifying it
        #[arg(long)]
        check: bool,

        /// Explicit RAG sync date (defaults to manual-corpus.tsv mtime date or current date)
        #[arg(long)]
        rag_sync: Option<String>,

        /// Explicit target files to synchronize (defaults to specs/ engineering scripts index and docs)
        #[arg(value_name = "PATHS")]
        paths: Vec<PathBuf>,
    },

    /// Generates systemd Quadlet files (.pod, .container, .network, .volume, .image) from mios.toml SSOT
    #[command(name = "pod-quadlets", aliases = ["pod-gen", "generate-pod-quadlets"])]
    PodQuadlets {
        /// Repository root directory
        #[arg(long)]
        root: Option<PathBuf>,

        /// Check mode: verify committed units match SSOT without modifying them
        #[arg(long)]
        check: bool,

        /// List mode: print all generated unit filenames
        #[arg(long)]
        list: bool,
    },

    /// Renders tmux theme configuration (usr/share/mios/tmux/mios-theme.tmux.conf) from SSOT
    #[command(name = "render-tmux-theme", aliases = ["tmux-theme"])]
    RenderTmuxTheme {
        /// Repository root directory
        #[arg(long)]
        root: Option<PathBuf>,

        /// Check mode: verify committed mios-theme.tmux.conf matches projection
        #[arg(long)]
        check: bool,

        /// Check fixture mode: alias matching legacy script CLI (`--check-fixture <ROOT>`)
        #[arg(long)]
        check_fixture: Option<PathBuf>,

        /// Write fixture mode: alias matching legacy script CLI (`--write-fixture <ROOT>`)
        #[arg(long)]
        write_fixture: Option<PathBuf>,

        /// Output path for tmux configuration file
        #[arg(long, aliases = ["output", "out"])]
        out: Option<PathBuf>,

        /// Visual styling format for status line segments (powerline, rounded, minimal)
        #[arg(long)]
        style: Option<String>,

        /// Status bar screen position (bottom, top)
        #[arg(long, alias = "position")]
        status_position: Option<String>,
    },

    /// Renders btop system monitor theme (etc/btop/themes/mios.theme) from SSOT
    #[command(name = "render-btop-theme", aliases = ["btop-theme"])]
    RenderBtopTheme {
        /// Repository root directory
        #[arg(long)]
        root: Option<PathBuf>,

        /// Check mode: verify committed btop theme matches projection
        #[arg(long)]
        check: bool,

        /// Output path for btop theme file
        #[arg(long, aliases = ["output", "out"])]
        out: Option<PathBuf>,
    },
    /// Render the SSOT fastfetch telemetry config; mock/dry-run do not write
    #[command(name = "render-fastfetch", aliases = ["fastfetch", "fastfetch-gen"])]
    RenderFastfetch {
        #[arg(long)]
        root: Option<PathBuf>,
        #[arg(long)]
        check: bool,
        #[arg(long, alias = "output")]
        out: Option<PathBuf>,
        #[arg(long, default_value = "small")]
        logo_type: String,
        #[arg(long)]
        mock: bool,
        #[arg(long)]
        dry_run: bool,
        #[arg(long)]
        generate: bool,
    },
}

fn resolve_root(cli_root: Option<PathBuf>) -> PathBuf {
    if let Some(r) = cli_root {
        return r;
    }
    if let Ok(r) = env::var("MIOS_DRIFT_ROOT") {
        if !r.trim().is_empty() {
            return PathBuf::from(r.trim());
        }
    }
    if let Ok(r) = env::var("MIOS_ROOT") {
        if !r.trim().is_empty() {
            return PathBuf::from(r.trim());
        }
    }
    PathBuf::from(".")
}

fn run_cosign_policy(root: &Path, check_mode: bool) -> Result<(), (String, i32)> {
    let ssot_path = root.join("usr/share/mios/mios.toml");
    let target_path = root.join("usr/lib/containers/policy.json");

    if !ssot_path.is_file() {
        return Err((format!("{} not found", ssot_path.display()), 1));
    }

    let toml_bytes = fs::read(&ssot_path).map_err(|e| {
        (
            format!("{} could not be read: {}", ssot_path.display(), e),
            1,
        )
    })?;
    let toml_str = std::str::from_utf8(&toml_bytes).map_err(|e| {
        (
            format!("{} could not be read: {}", ssot_path.display(), e),
            1,
        )
    })?;
    let val: toml::Value = toml::from_str(toml_str).map_err(|e| {
        (
            format!("{} could not be read: {}", ssot_path.display(), e),
            1,
        )
    })?;

    let sigstore = val
        .get("security")
        .and_then(|s| s.get("sigstore"))
        .and_then(|s| s.as_table())
        .ok_or_else(|| {
            (
                "mios.toml declares no [security.sigstore] table".to_string(),
                1,
            )
        })?;

    let policy_mode = sigstore
        .get("policy_mode")
        .and_then(|p| p.as_str())
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
        .ok_or_else(|| {
            (
                "[security.sigstore].policy_mode is absent or not a string".to_string(),
                1,
            )
        })?;

    let json_val = serde_json::json!({
        "default": [
            {
                "type": policy_mode
            }
        ]
    });
    let rendered = serde_json::to_string_pretty(&json_val)
        .map_err(|e| (format!("Failed to format JSON: {e}"), 1))?
        + "\n";

    if check_mode {
        if !target_path.is_file() {
            return Err((format!("{} does not exist", target_path.display()), 1));
        }
        let committed = fs::read_to_string(&target_path)
            .map_err(|e| (format!("Failed to read {}: {e}", target_path.display()), 1))?;
        if committed != rendered {
            return Err((
                format!(
                    "{} is out of sync with [security.sigstore] SSOT -- regenerate: mios-gen cosign-policy",
                    target_path.display()
                ),
                1,
            ));
        }
        println!("[OK] usr/lib/containers/policy.json is in sync with SSOT");
        return Ok(());
    }

    if let Some(parent) = target_path.parent() {
        fs::create_dir_all(parent).map_err(|e| {
            (
                format!("Failed to create directory {}: {e}", parent.display()),
                1,
            )
        })?;
    }
    fs::write(&target_path, rendered.as_bytes())
        .map_err(|e| (format!("Failed to write {}: {e}", target_path.display()), 1))?;

    println!("Generated {}", target_path.display());
    Ok(())
}

fn get_agent_user(root: &Path) -> String {
    if let Ok(u) = env::var("MIOS_AGENT_USER") {
        let trimmed = u.trim();
        if !trimmed.is_empty() {
            return trimmed.to_string();
        }
    }

    let service_path = root.join("usr/lib/systemd/system/mios-agent-pipe.service");
    if let Ok(content) = fs::read_to_string(&service_path) {
        for line in content.lines() {
            if let Some(rest) = line.strip_prefix("User=") {
                let trimmed = rest.trim();
                if !trimmed.is_empty() {
                    return trimmed.to_string();
                }
            }
        }
    }

    "mios-ai".to_string()
}

fn build_egress_ruleset(mode: &str, allow: &[String], user: &str) -> (String, &'static str) {
    let (final_rule, note, normalized_mode) = match mode {
        "enforce" => (
            "        log prefix \"mios-egress-drop \" drop",
            "ENFORCE: the agent's non-allowed external egress is logged + DROPPED.",
            "enforce",
        ),
        "audit" => (
            "        log prefix \"mios-egress-audit \" accept",
            "AUDIT: the agent's external egress is LOGGED then accepted (observe only).",
            "audit",
        ),
        _ => (
            "        accept   # mode=off -> no-op even if applied",
            "OFF: informational ruleset; applying it changes nothing.",
            "off",
        ),
    };

    let mut allow_rules = String::new();
    let mut v4: Vec<&str> = allow
        .iter()
        .map(|s| s.as_str())
        .filter(|s| !s.contains(':'))
        .collect();
    let mut v6: Vec<&str> = allow
        .iter()
        .map(|s| s.as_str())
        .filter(|s| s.contains(':'))
        .collect();
    v4.sort_unstable();
    v6.sort_unstable();

    if !v4.is_empty() {
        allow_rules.push_str(&format!(
            "        ip daddr {{ {} }} accept\n",
            v4.join(", ")
        ));
    }
    if !v6.is_empty() {
        allow_rules.push_str(&format!(
            "        ip6 daddr {{ {} }} accept\n",
            v6.join(", ")
        ));
    }

    let content = format!(
        r#"# AI-hint: GENERATED nftables egress firewall for the MiOS agent (#54). DO NOT EDIT -- regenerate via mios-gen egress-firewall. {note}
table inet mios_egress {{
    chain output {{
        type filter hook output priority filter; policy accept;
        meta skuid != "{user}" accept
        oifname "lo" accept
        ip daddr 127.0.0.0/8 accept
        ip6 daddr ::1 accept
        ip daddr 100.64.0.0/10 accept
        ip daddr 172.16.0.0/12 accept
{allow_rules}{final_rule}
    }}
}}
"#
    );

    (content, normalized_mode)
}

fn run_egress_firewall(root: &Path) -> Result<(), (String, i32)> {
    let toml_path = env::var("MIOS_TOML")
        .map(PathBuf::from)
        .unwrap_or_else(|_| root.join("usr/share/mios/mios.toml"));
    let out_path = env::var("MIOS_EGRESS_OUT")
        .map(PathBuf::from)
        .unwrap_or_else(|_| root.join("usr/share/mios/security/egress.nft"));

    let toml_bytes = fs::read(&toml_path)
        .map_err(|e| (format!("Failed to read {}: {e}", toml_path.display()), 1))?;
    let toml_str = std::str::from_utf8(&toml_bytes)
        .map_err(|e| (format!("Failed to read {}: {e}", toml_path.display()), 1))?;
    let val: toml::Value = toml::from_str(toml_str)
        .map_err(|e| (format!("Failed to parse {}: {e}", toml_path.display()), 1))?;

    let egress_table = val
        .get("security")
        .and_then(|s| s.get("egress"))
        .and_then(|e| e.as_table());

    let mode = egress_table
        .and_then(|e| e.get("mode"))
        .and_then(|m| m.as_str())
        .unwrap_or("off")
        .trim()
        .to_ascii_lowercase();

    let mut allow = Vec::new();
    if let Some(arr) = egress_table
        .and_then(|e| e.get("allow"))
        .and_then(|a| a.as_array())
    {
        for item in arr {
            let s = match item {
                toml::Value::String(s) => s.trim().to_string(),
                toml::Value::Integer(i) => i.to_string(),
                _ => item.to_string(),
            };
            if !s.is_empty() {
                allow.push(s);
            }
        }
    }

    let user = get_agent_user(root);
    let (ruleset, normalized_mode) = build_egress_ruleset(&mode, &allow, &user);

    if let Some(parent) = out_path.parent() {
        fs::create_dir_all(parent).map_err(|e| {
            (
                format!("Failed to create directory {}: {e}", parent.display()),
                1,
            )
        })?;
    }
    fs::write(&out_path, ruleset.as_bytes())
        .map_err(|e| (format!("Failed to write {}: {e}", out_path.display()), 1))?;

    println!(
        "[egress-fw] wrote {} (mode={}, user={}, allow={})",
        out_path.display(),
        normalized_mode,
        user,
        allow.len()
    );
    Ok(())
}

fn main() -> ExitCode {
    let cli = Cli::parse();
    let (subcommand, target, result) = match cli.command {
        Commands::CosignPolicy { root, check } => {
            let r = resolve_root(root);
            (
                "cosign-policy",
                "usr/lib/containers/policy.json",
                run_cosign_policy(&r, check),
            )
        }
        Commands::EgressFirewall { root } => {
            let r = resolve_root(root);
            (
                "egress-firewall",
                "usr/share/mios/security/egress.nft",
                run_egress_firewall(&r),
            )
        }
        Commands::GateIndex {
            root,
            script,
            output,
            check,
        } => {
            let r = resolve_root(root);
            (
                "gate-index",
                "usr/share/mios/reference/drift-gate-index.tsv",
                gate_index::run_gate_index(&r, script, output, check),
            )
        }
        Commands::PipelineIndex { root, check } => {
            let r = resolve_root(root);
            (
                "pipeline-index",
                "usr/share/mios/reference/pipeline-index.tsv",
                pipeline_index::run_pipeline_index(&r, check),
            )
        }
        Commands::AdrIndex { root, check } => {
            let r = resolve_root(root);
            (
                "adr-index",
                "ADR.md",
                adr_index::run_adr_index(&r, check, cli.format == "json"),
            )
        }
        Commands::MetalVsHosted { root, check } => {
            let r = resolve_root(root);
            (
                "metal-vs-hosted",
                "usr/share/doc/mios/reference/metal-vs-hosted.md",
                metal_vs_hosted::run_metal_vs_hosted(&r, check, cli.format == "json"),
            )
        }
        Commands::RoadmapIndex { root, check } => {
            let r = resolve_root(root);
            (
                "roadmap-index",
                "ROADMAP.md",
                roadmap_index::run_roadmap_index(&r, check, cli.format == "json"),
            )
        }
        Commands::AiManifest { root, check } => {
            let r = resolve_root(root);
            (
                "ai-manifest",
                "manifests",
                ai_manifest::run_ai_manifest(&r, check, cli.format == "json"),
            )
        }
        Commands::RenderPorts {
            root,
            toml,
            check,
            print_ports,
        } => {
            let r = resolve_root(root);
            (
                "render-ports",
                "usr/share/mios/mios.toml [ports]",
                match render_ports::run_render_ports(&r, toml.as_deref(), check, print_ports) {
                    Ok((msg, _)) => {
                        if cli.format != "json" {
                            print!("{msg}");
                        }
                        Ok(())
                    }
                    Err((msg, code)) => Err((msg, code)),
                },
            )
        }
        Commands::RenderDesktop { root, check } => {
            let r = resolve_root(root);
            (
                "render-desktop",
                "usr/share/applications/*.desktop",
                match render_desktop::run_render_desktop(&r, check) {
                    Ok((msg, _)) => {
                        if cli.format != "json" && !msg.is_empty() {
                            println!("{msg}");
                        }
                        Ok(())
                    }
                    Err((msg, code)) => Err((msg, code)),
                },
            )
        }
        Commands::RenderGlobals { root, check } => {
            let r = resolve_root(root);
            (
                "render-globals",
                "automation/lib/globals.{sh,ps1}",
                match render_globals::run_render_globals(&r, check) {
                    Ok((msg, _)) => {
                        if cli.format != "json" && !msg.is_empty() {
                            println!("{msg}");
                        }
                        Ok(())
                    }
                    Err((msg, code)) => Err((msg, code)),
                },
            )
        }
        Commands::RenderManpages {
            root,
            check,
            validate,
        } => {
            let r = resolve_root(root);
            (
                "render-manpages",
                "usr/share/man/**",
                match render_manpages::run_render_manpages(&r, check, validate) {
                    Ok((msg, _)) => {
                        if cli.format != "json" && !msg.is_empty() {
                            println!("{msg}");
                        }
                        Ok(())
                    }
                    Err((msg, code)) => Err((msg, code)),
                },
            )
        }
        Commands::BibConfigs { root, check } => {
            let r = resolve_root(root);
            (
                "bib-configs",
                "config/artifacts/{bib,iso}.toml",
                match bib_configs::run_bib_configs(&r, check) {
                    Ok(res) => {
                        if cli.format != "json" {
                            if check {
                                println!("PASS: BIB artifact configs in sync with mios.toml SSOT.");
                            } else {
                                println!(
                                    "Updated BIB configs with SSOT sizes: raw={}, iso={}.",
                                    res.raw_size, res.iso_size
                                );
                            }
                        }
                        Ok(())
                    }
                    Err(msg) => Err((msg, 1)),
                },
            )
        }
        Commands::CargoManifests { root, check } => {
            let r = resolve_root(root);
            (
                "cargo-manifests",
                "tools/native/Cargo.toml",
                match cargo_manifests::run_cargo_manifests(&r, check) {
                    Ok(res) => {
                        if cli.format != "json" {
                            if check {
                                println!("PASS: tools/native/Cargo.toml matches its generator projection.");
                            } else {
                                println!(
                                    "Updated tools/native/Cargo.toml: {} members, version {}.",
                                    res.members_count, res.version
                                );
                            }
                        }
                        Ok(())
                    }
                    Err(msg) => Err((msg, 1)),
                },
            )
        }
        Commands::PipeBoundaries { root, check } => {
            let r = resolve_root(root);
            (
                "pipe-boundaries",
                "usr/share/mios/pipe-boundaries.manifest.json",
                match pipe_boundaries::run_pipe_boundaries(&r, check) {
                    Ok(res) => {
                        if cli.format != "json" {
                            if check {
                                println!(
                                    "[gen-pipe-boundary-manifest] usr/share/mios/pipe-boundaries.manifest.json matches the tree ({} modules).",
                                    res.modules_count
                                );
                            } else {
                                println!(
                                    "[gen-pipe-boundary-manifest] Emitted usr/share/mios/pipe-boundaries.manifest.json with {} modules.",
                                    res.modules_count
                                );
                            }
                        }
                        Ok(())
                    }
                    Err(msg) => Err((msg, 1)),
                },
            )
        }
        Commands::StandardizeDocs { root, check, paths } => {
            let r = resolve_root(root);
            (
                "standardize-docs",
                "specs/**/*.md",
                match standardize_docs::run_standardize_docs(&r, check, &paths) {
                    Ok(res) => {
                        if cli.format != "json" {
                            if check {
                                println!(
                                    "[standardize-docs] specs/ markdown documentation is standardized ({} files).",
                                    res.scanned
                                );
                            } else {
                                println!(
                                    "[standardize-docs] Standardized {} files ({} modified).",
                                    res.scanned, res.modified
                                );
                            }
                        }
                        Ok(())
                    }
                    Err(msg) => Err((msg, 1)),
                },
            )
        }
        Commands::SyncWiki {
            root,
            check,
            rag_sync,
            paths,
        } => {
            let r = resolve_root(root);
            (
                "sync-wiki",
                "specs",
                match sync_wiki::run_sync_wiki(&r, check, rag_sync.as_deref(), &paths) {
                    Ok(res) => {
                        if cli.format != "json" {
                            if check {
                                println!(
                                    "[sync-wiki] documentation embeds are in sync ({} files scanned).",
                                    res.scanned
                                );
                            } else {
                                println!(
                                    "[sync-wiki] Synchronized {} files ({} modified).",
                                    res.scanned, res.modified
                                );
                            }
                        }
                        Ok(())
                    }
                    Err(msg) => Err((msg, 1)),
                },
            )
        }
        Commands::PodQuadlets { root, check, list } => {
            let r = resolve_root(root);
            (
                "pod-quadlets",
                "usr/share/containers/systemd/*.{pod,container,network,volume,image}",
                match pod_quadlets::run_pod_quadlets(&r, check, list) {
                    Ok(res) => {
                        if cli.format != "json" {
                            if list {
                                for f in &res.listed_files {
                                    println!("{f}");
                                }
                            } else if check {
                                println!(
                                    "[pod-gen] all {} Quadlet unit(s) match SSOT",
                                    res.active_units
                                );
                            } else {
                                for w in &res.written_files {
                                    println!("[pod-gen]   wrote {}", w.display());
                                }
                                for rm in &res.removed_files {
                                    println!("[pod-gen]   removed {}", rm.display());
                                }
                                println!(
                                    "[pod-gen] wrote {} Quadlet unit(s) to {}",
                                    res.wrote,
                                    res.out_dir.display()
                                );
                            }
                        }
                        Ok(())
                    }
                    Err(msg) => Err((msg, 1)),
                },
            )
        }
        Commands::RenderTmuxTheme {
            root,
            check,
            check_fixture,
            write_fixture,
            out,
            style,
            status_position,
        } => {
            let (effective_root, is_check) = if let Some(cf) = check_fixture {
                (cf, true)
            } else if let Some(wf) = write_fixture {
                (wf, false)
            } else {
                (resolve_root(root), check)
            };

            (
                "render-tmux-theme",
                "usr/share/mios/tmux/mios-theme.tmux.conf",
                match tmux_theme::run_render_tmux_theme(
                    &effective_root,
                    is_check,
                    style.as_deref(),
                    status_position.as_deref(),
                    out.as_deref(),
                ) {
                    Ok(res) => {
                        if cli.format != "json" {
                            if is_check {
                                println!("[tmux-theme] tmux theme matches SSOT ({})", res.style);
                            } else {
                                println!(
                                    "[tmux-theme] SUCCESS: Generated tmux theme ({} lines) style '{}'",
                                    res.config_lines, res.style
                                );
                                if let Some(p) = res.output_path {
                                    println!("  Saved config: {}", p.display());
                                }
                            }
                        }
                        Ok(())
                    }
                    Err(msg) => Err((msg, 1)),
                },
            )
        }
        Commands::RenderBtopTheme { root, check, out } => {
            let r = resolve_root(root);
            (
                "render-btop-theme",
                "etc/btop/themes/mios.theme",
                match btop_theme::run_render_btop_theme(&r, check, out.as_deref()) {
                    Ok(res) => {
                        if cli.format != "json" {
                            if check {
                                println!("[btop-theme] btop theme matches SSOT");
                            } else {
                                println!(
                                    "[btop-theme] SUCCESS: Generated btop theme ({} keys, {} bytes)",
                                    res.keys_count, res.theme_len
                                );
                                println!("  Saved config: {}", res.target.display());
                            }
                        }
                        Ok(())
                    }
                    Err(msg) => Err((msg, 1)),
                },
            )
        }
        Commands::RenderFastfetch {
            root,
            check,
            out,
            logo_type,
            mock,
            dry_run,
            generate: _,
        } => {
            let r = resolve_root(root);
            (
                "render-fastfetch",
                fastfetch::DEFAULT_FASTFETCH_TARGET,
                match fastfetch::run_render_fastfetch(
                    &r,
                    check,
                    out.as_deref(),
                    Some(&logo_type),
                    mock,
                    dry_run,
                ) {
                    Ok(res) => {
                        if cli.format != "json" {
                            if check {
                                println!("[fastfetch] fastfetch configuration matches projection");
                            } else {
                                println!("[fastfetch] SUCCESS: Generated Fastfetch config ({} lines, {} bytes)", res.lines_count, res.jsonc_len);
                                if !mock && !dry_run {
                                    if let Some(target) = &res.target {
                                        println!("  Saved config: {}", target.display());
                                    }
                                }
                            }
                        }
                        Ok(())
                    }
                    Err(msg) => Err((msg, 1)),
                },
            )
        }
    };

    if cli.format == "json" {
        match &result {
            Ok(()) => {
                let json = serde_json::json!({
                    "status": "clean",
                    "subcommand": subcommand,
                    "target": target,
                    "violations": 0
                });
                println!("{}", serde_json::to_string_pretty(&json).unwrap());
            }
            Err((msg, code)) => {
                let json = serde_json::json!({
                    "status": "violation",
                    "subcommand": subcommand,
                    "target": target,
                    "violations": 1,
                    "message": msg,
                    "exit_code": code
                });
                println!("{}", serde_json::to_string_pretty(&json).unwrap());
            }
        }
    } else if let Err((msg, _)) = &result {
        if msg.starts_with("VIOLATION:")
            || msg.starts_with("Error:")
            || msg.starts_with("ERROR: BIB")
            || msg.starts_with("generate-metal-vs-hosted:")
            || msg.starts_with("[render-ports]")
            || msg.starts_with("[render-desktop]")
            || msg.starts_with("[render-globals]")
            || msg.starts_with("[render-manpages]")
            || msg.starts_with("[generate-cargo-manifests]")
            || msg.starts_with("[gen-pipe-boundary-manifest]")
            || msg.starts_with("MISSING ")
            || msg.starts_with("[sync-wiki]")
            || msg.starts_with("Wiki documentation embeds are STALE")
            || msg.starts_with("man pages out of sync")
            || msg.starts_with("man page validation failed")
            || msg.starts_with("[tmux-theme]")
            || msg.starts_with("[btop-theme]")
            || msg.contains("mios-theme.tmux.conf:")
            || msg.contains("ADR SSOT consistency check failed:")
        {
            eprintln!("{msg}");
        } else {
            eprintln!("Error: generate-{subcommand}: {msg}");
        }
    }

    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err((_, code)) => ExitCode::from(code as u8),
    }
}
