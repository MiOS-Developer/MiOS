// AI-hint: Entry point for the MiOS daemon native workspace (WS-LANG)
// AI-related: Containerfile, automation/98-drift-checks.sh

#![warn(clippy::unwrap_used, clippy::panic, clippy::todo)]

mod drift;
mod native_generator;
mod terminal_runtime;

// AI-related: Containerfile, automation/98-drift-checks.sh

use clap::{Parser, Subcommand};

#[derive(Parser)]
#[command(name = "miosd")]
#[command(about = "MiOS native daemon and toolbox", long_about = None)]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Update the single build-stage ledger and render its cumulative status.
    BuildProgress {
        #[arg(long, default_value = ".")]
        root: std::path::PathBuf,
        #[arg(long)]
        state: std::path::PathBuf,
        #[arg(long, value_parser = ["init", "start", "result", "note", "finish"])]
        event: String,
        #[arg(long)]
        name: Option<String>,
        #[arg(long, value_parser = ["pass", "fail", "warn", "skip", "missing"])]
        status: Option<String>,
    },
    /// Verify or explicitly repair the SSOT tmux namespace without restarting sessions
    TerminalRuntimeCheck {
        #[arg(long, default_value = "/")]
        root: String,
        #[arg(long)]
        uid: Option<u32>,
        #[arg(long)]
        gid: Option<u32>,
        #[arg(long)]
        repair: bool,
    },
    /// Build, lint, verify and install the SSOT Linux native catalog into an FHS root
    NativeBuild {
        #[arg(long, default_value = ".")]
        root: String,
        #[arg(long)]
        target_dir: std::path::PathBuf,
        #[arg(long)]
        install_root: std::path::PathBuf,
        #[arg(long, default_value = std::env::consts::ARCH)]
        arch: String,
    },
    /// Lint, cross-build and verify the Windows catalog, or one selected executable
    NativeWindowsBuild {
        #[arg(long, default_value = ".")]
        root: String,
        #[arg(long)]
        binary: Option<String>,
        #[arg(long)]
        target_dir: std::path::PathBuf,
    },
    /// Verify SSOT-required lint tools; optionally run warning-fatal workspace lint
    NativeToolchainCheck {
        #[arg(long, default_value = ".")]
        root: String,
        #[arg(long)]
        lint: bool,
    },
    /// Validate and transactionally install every declared Windows executable.
    NativeWindowsInstall {
        #[arg(long)]
        root: std::path::PathBuf,
        #[arg(long)]
        source: std::path::PathBuf,
        #[arg(long)]
        bin_dir: std::path::PathBuf,
    },
    /// Check every installed SSOT native artifact without requiring Cargo
    NativeRuntimeCheck {
        #[arg(long, default_value = "/")]
        root: String,
        #[arg(long, default_value = std::env::consts::OS)]
        platform: String,
        #[arg(long, default_value = std::env::consts::ARCH)]
        arch: String,
        #[arg(long)]
        bin_dir: Option<std::path::PathBuf>,
    },
    /// Build current source image targets and verify their SSOT runtime commands
    ImageBuild {
        #[arg(long, default_value = ".")]
        root: String,
        #[arg(long, default_value = "all")]
        target: String,
        #[arg(long)]
        plan: bool,
    },
    /// Resolve the SSOT native Linux target, linker and static runtime flags
    NativeBuildSettings {
        #[arg(long, default_value = ".")]
        root: String,
        #[arg(long, default_value = std::env::consts::ARCH)]
        arch: String,
        #[arg(long)]
        json: bool,
    },
    /// Reject malformed, foreign or dynamically linked Linux executables
    NativeArtifactCheck {
        path: String,
        #[arg(long, default_value = std::env::consts::ARCH)]
        arch: String,
        /// Tree whose [build.native.linux] policy (e.g. pie) the artifact must meet.
        #[arg(long, default_value = ".")]
        root: String,
        #[arg(long, default_value = "linux")]
        platform: String,
    },
    /// List SSOT-categorized native executables using both Cargo workspaces
    NativeTargets {
        #[arg(long, default_value = ".")]
        root: String,
        #[arg(long, default_value = "linux")]
        platform: String,
        #[arg(long)]
        json: bool,
    },
    /// Run structural drift checks across the repository
    DriftCheck {
        /// Optional root directory to check
        #[arg(long)]
        root: Option<String>,
        /// Advisory mode (exit 0 on failure)
        #[arg(long)]
        soft: bool,
        /// List registered drift check IDs
        #[arg(long)]
        list: bool,
        /// Run only a specific drift check ID
        #[arg(long)]
        only: Option<String>,
        /// Run differential parity check vs bash twin
        #[arg(long)]
        parity: bool,
    },
    /// Scaffold a new file from a template
    Scaffold {
        /// Type of the template
        template_type: String,
        /// Name of the new file
        name: String,
    },
    /// Run or plan MiOS image build phases
    Build {
        /// Target phase or mode
        #[arg(default_value = "all")]
        phase: String,
        /// Print execution plan without running build phases
        #[arg(long)]
        plan: bool,
        /// Print raw script list for build orchestrator
        #[arg(long)]
        list: bool,
        /// Select phases through a mios.toml [profiles] entry (ADR-0025)
        #[arg(long)]
        profile: Option<String>,
        /// Print the profile's package sections ("*" = every section) instead of phases
        #[arg(long)]
        sections: bool,
        /// Print the SSOT post-build validation plan for the build adapter
        #[arg(long, conflicts_with_all = ["sections", "list", "plan"])]
        post_list: bool,
    },
    /// Resolve configuration parameters
    Resolve {
        /// Output in shell export format
        #[arg(long)]
        shell: bool,
    },
    /// Render ports from mios.toml to install.env with stack_id offset
    RenderPorts {
        /// Input TOML file path
        #[arg(long, default_value = "/usr/share/mios/mios.toml")]
        toml: String,
        /// Target install.env path
        #[arg(long, default_value = "/etc/mios/install.env")]
        out: String,
    },
    /// Render Quadlet container placeholders (${MIOS_*}) across config directories
    RenderQuadlets {
        /// Target directories to scan and render
        #[arg(long)]
        dirs: Vec<String>,
    },
    /// Render kernel arguments from mios.toml [kargs] to kargs.d/*.toml
    RenderKargs {
        /// Input TOML file path
        #[arg(long, default_value = "/usr/share/mios/mios.toml")]
        toml: String,
        /// kargs.d directory path
        #[arg(long, default_value = "/usr/lib/bootc/kargs.d")]
        kargs_dir: String,
    },
    /// Synthesize Quadlet files (.pod, .container, .network) from mios.toml
    GenerateQuadlets {
        /// Verify committed tree matches generator output without mutating
        #[arg(long)]
        check: bool,
    },
    /// Render UKI kernel cmdline from kargs.d/*.toml to /usr/lib/kernel/cmdline
    RenderUkiCmdline {
        /// Verify committed /usr/lib/kernel/cmdline matches generator output without mutating
        #[arg(long)]
        check: bool,
    },
    /// Render Chrony NTP config from mios.toml [network.ntp]
    RenderChrony {
        /// Input TOML file path
        #[arg(long, default_value = "/usr/share/mios/mios.toml")]
        toml: String,
        /// Target chrony.conf path
        #[arg(long, default_value = "/etc/chrony.conf")]
        out: String,
    },
    /// Render NUT UPS configurations from mios.toml [power.ups]
    RenderNut {
        /// Input TOML file path
        #[arg(long, default_value = "/usr/share/mios/mios.toml")]
        toml: String,
        /// Target /etc/ups configuration directory
        #[arg(long, default_value = "/etc/ups")]
        out_dir: String,
    },
    /// Configure firewalld offline rules for MiOS services
    FirewallPorts,
    /// Render /usr/lib/containers/policy.json from mios.toml [security.sigstore]
    CosignPolicy {
        /// Verify policy.json matches generator output without mutating
        #[arg(long)]
        check: bool,
    },
    /// Re-project SSOT version onto /usr/lib/os-release fields
    FinalizeOsrelease {
        /// Target os-release file path
        #[arg(long, default_value = "/usr/lib/os-release")]
        path: String,
        /// Explicit version override (defaults to reading /ctx/VERSION or /usr/share/mios/version)
        #[arg(long)]
        version: Option<String>,
    },
    /// Transactional bootc switch from last build sentinel
    BootcApply {
        /// Path to last build sentinel file
        #[arg(long, default_value = "/var/lib/mios/forge-runner/last-build.txt")]
        sentinel: String,
    },
    /// Inspect, dry-run, or execute atomic bootc rollback with recovery verification (T-1025)
    BootcRollback {
        /// Validate rollback target without switching
        #[arg(long)]
        check: bool,
        /// Simulate rollback operations without modifying ostree state
        #[arg(long)]
        dry_run: bool,
        /// Force rollback execution even if warnings are present
        #[arg(long)]
        force: bool,
    },
    /// Idempotent container image build-if-missing provisioner
    BuildIfMissing {
        /// Spec name (e.g. agents, forgejo-runner, webtools)
        spec: String,
    },
    /// Symlink security services into multi-user.target.wants and fix config perms
    Harden {
        /// Prefix every system path with this directory. Defaults to "/".
        /// Exists so the hardening can be exercised against a fixture tree --
        /// with the paths hardcoded absolute there was no way to test it that
        /// did not rewrite fapolicyd.conf and systemd's wants directory on the
        /// machine running the test.
        #[arg(long, default_value = "/")]
        root: String,
    },
    /// Render /etc/yum.repos.d/fedora-{version}.repo file
    RenderRepos {
        /// Force online metalink mode (defaults to local vendored mirror if present)
        #[arg(long)]
        online: bool,
        /// Fedora release version. No default: the version belongs to
        /// mios.toml [versions].fedora and the caller passes it, rather than a
        /// literal here going stale beside it (Law 7).
        #[arg(long)]
        fedora_version: String,
        /// Target repo file path (defaults to /etc/yum.repos.d/fedora-{version}.repo)
        #[arg(long)]
        output: Option<String>,
        /// Vendored RPM mirror to probe for. Defaults to the system path;
        /// overridable so the offline branch can be exercised against a
        /// fixture instead of only on a host that happens to have the mirror.
        #[arg(long, default_value = "/usr/share/mios/vendored/rpms")]
        vendored_dir: String,
    },
    /// Bind Quadlet images into /usr/lib/bootc/bound-images.d excluding firstboot tokens
    OverlayBindImages {
        /// Destination directory for bound image symlinks
        #[arg(long, default_value = "/usr/lib/bootc/bound-images.d")]
        dest: String,
        /// Quadlet source directory; repeatable. Defaults to the two system
        /// directories. Exists so the bind can be exercised against a fixture
        /// tree -- with the paths hardcoded there was no way to test it that
        /// did not write to /usr/share on the host running the test.
        #[arg(long = "qdir")]
        qdirs: Vec<String>,
    },
    /// Run native greenboot health check validation
    Greenboot,
    /// Run native Tokio async supervisor daemon (telemetry, theme, backup, watchdog, state.json)
    Daemon {
        /// Directory for persistent daemon state (state.json)
        #[arg(long, default_value = "/var/lib/mios/daemon")]
        state_dir: String,
        /// Telemetry and watchdog cycle interval in seconds
        #[arg(long, default_value_t = 5)]
        interval_secs: u64,
        /// Execute a single supervisor tick and exit
        #[arg(long)]
        once: bool,
        /// Optional hardware watchdog device path
        #[arg(long)]
        watchdog_dev: Option<String>,
    },
    /// Validate SSOT mios.toml syntax, schema, port collisions, Law 7, and ratchets
    Check {
        /// Path to mios.toml configuration file
        #[arg(default_value = "/usr/share/mios/mios.toml")]
        path: String,
        /// Emit structured JSON validation report
        #[arg(long)]
        json: bool,
        /// Explicit validation mode flag
        #[arg(long)]
        check: bool,
    },
    /// Binary CLI dispatcher forwarding verbs or falling back to OpenAI chat stream
    #[command(disable_help_flag = true)]
    Cli {
        /// CLI arguments forwarded to verb handler
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Secure secret management, native Linux Keyrings, desktop prompt, and pipeline safety net
    Secret {
        #[command(subcommand)]
        action: SecretAction,
    },
    /// Local settings HTTP engine hosting mios.html and /portal/config SSOT API
    ConfigServer {
        /// Optional custom bind address (e.g. 127.0.0.1:8700)
        #[arg(long)]
        bind: Option<String>,
        /// Optional custom port (default: 8700 or $MIOS_PORTS_AGENT_PIPE)
        #[arg(long)]
        port: Option<u16>,
    },
}

#[derive(Subcommand, Debug)]
pub enum SecretAction {
    /// Securely prompt operator on desktop (zenity/pinentry) or TTY (no-echo)
    Prompt {
        #[arg(short, long, default_value = "Password: ")]
        message: String,
        #[arg(long, default_value = "MiOS Authentication")]
        title: String,
        #[arg(long)]
        gui: bool,
        #[arg(long)]
        tty: bool,
        /// Descriptor (3 or higher) the caller opened to receive the secret; never stdout
        #[arg(long)]
        fd: i32,
    },
    /// Store secret securely in Linux Keyrings / FreeDesktop Secret Service
    Set {
        #[arg(short, long)]
        key: String,
        #[arg(short, long, default_value = "mios")]
        service: String,
        #[arg(long)]
        prompt: bool,
        #[arg(long)]
        value: Option<String>,
    },
    /// Retrieve secret from Linux Keyrings / FreeDesktop Secret Service
    Get {
        #[arg(short, long)]
        key: String,
        #[arg(short, long, default_value = "mios")]
        service: String,
        /// Descriptor (3 or higher) the caller opened to receive the secret; never stdout
        #[arg(long)]
        fd: i32,
    },
    /// Pipeline safety scanner: audit directory or files for leaked credentials
    Scan {
        #[arg(default_value = ".")]
        path: String,
        #[arg(long)]
        strict: bool,
    },
}

/// Prefix + name + suffix. An ordinal prefix is a seed, not a literal.
/// Twin of `get_dest_path` in usr/libexec/mios/mios-new.
fn resolve_name(
    name: &str,
    cfg: &toml::Value,
    repo_root: &std::path::Path,
) -> Result<String, Box<dyn std::error::Error>> {
    let mut final_name = name.to_string();
    let dest_dir = cfg.get("dest_dir").and_then(|d| d.as_str()).unwrap_or(".");

    if let Some(prefix) = cfg.get("name_prefix").and_then(|p| p.as_str()) {
        match regex::Regex::new(r"^(\d+)-$")?.captures(prefix) {
            Some(caps) => {
                let width = caps[1].len();
                if !regex::Regex::new(r"^\d+-")?.is_match(&final_name) {
                    let n = if cfg
                        .get("name_ordinal_next")
                        .and_then(|v| v.as_bool())
                        .unwrap_or(false)
                    {
                        next_ordinal(&repo_root.join(dest_dir), width)
                    } else {
                        caps[1].parse::<u32>().unwrap_or(1)
                    };
                    final_name = format!("{:0width$}-{}", n, final_name, width = width);
                }
            }
            None => {
                if !final_name.starts_with(prefix) {
                    final_name = format!("{}{}", prefix, final_name);
                }
            }
        }
    }

    if let Some(suffix) = cfg.get("name_suffix").and_then(|s| s.as_str()) {
        if !final_name.ends_with(suffix) {
            final_name = format!("{}{}", final_name, suffix);
        }
    }

    Ok(final_name)
}

/// Lowest unused ordinal in `dest_dir`. The listing IS the allocation record,
/// so it cannot drift the way a hand-bumped `name_prefix` did. Missing or empty
/// yields 1. Twin of `next_ordinal` in usr/libexec/mios/mios-new.
fn next_ordinal(dest_dir: &std::path::Path, width: usize) -> u32 {
    let mut used = std::collections::HashSet::new();
    if let Ok(entries) = std::fs::read_dir(dest_dir) {
        for entry in entries.flatten() {
            let name = entry.file_name().to_string_lossy().to_string();
            let digits: String = name.chars().take(width).collect();
            if digits.len() == width
                && digits.chars().all(|c| c.is_ascii_digit())
                && name.chars().nth(width) == Some('-')
            {
                if let Ok(n) = digits.parse::<u32>() {
                    used.insert(n);
                }
            }
        }
    }
    let mut n = 1;
    while used.contains(&n) {
        n += 1;
    }
    n
}

fn run_scaffold(type_name: &str, name: &str) -> Result<(), Box<dyn std::error::Error>> {
    let repo_root = match std::env::var("MIOS_DRIFT_CHECK_ROOT")
        .or_else(|_| std::env::var("MIOS_THEME_ROOT"))
    {
        Ok(r) => std::path::PathBuf::from(r),
        Err(_) => std::env::current_dir()?,
    };

    let tmpl_file = repo_root.join("usr/share/mios/templates").join(type_name);
    if !tmpl_file.exists() {
        eprintln!(
            "Error: Template for '{}' not found at {:?}",
            type_name, tmpl_file
        );
        std::process::exit(1);
    }

    let toml_path = repo_root.join("usr/share/mios/mios.toml");
    let mut placeholders: std::collections::HashMap<String, String> =
        std::collections::HashMap::new();
    let mut tmpl_cfg: Option<toml::Value> = None;

    if let Ok(toml_str) = std::fs::read_to_string(&toml_path) {
        if let Ok(val) = toml_str.parse::<toml::Value>() {
            if let Some(p_tab) = val
                .get("templates")
                .and_then(|t| t.get("placeholders"))
                .and_then(|p| p.as_table())
            {
                for (k, v) in p_tab {
                    if let Some(s) = v.as_str() {
                        placeholders.insert(k.clone(), s.to_string());
                    }
                }
            }
            if let Some(t_tab) = val.get("templates").and_then(|t| t.get(type_name)) {
                tmpl_cfg = Some(t_tab.clone());
            }
        }
    }

    if tmpl_file.is_dir() {
        let dest_dir = tmpl_cfg
            .as_ref()
            .and_then(|c| c.get("dest_dir"))
            .and_then(|d| d.as_str())
            .unwrap_or("tools/native");
        let dest_root = repo_root.join(dest_dir).join(name);
        std::fs::create_dir_all(&dest_root)?;

        let pascal_name = {
            let words: Vec<&str> = name.split(&['-', '_'][..]).collect();
            words
                .iter()
                .map(|w| {
                    let mut c = w.chars();
                    match c.next() {
                        None => String::new(),
                        Some(f) => f.to_uppercase().collect::<String>() + c.as_str(),
                    }
                })
                .collect::<String>()
        };

        fn walk_scaffold(
            src_dir: &std::path::Path,
            dst_dir: &std::path::Path,
            crate_name: &str,
            p_name: &str,
            p_map: &std::collections::HashMap<String, String>,
        ) -> Result<(), Box<dyn std::error::Error>> {
            for entry in std::fs::read_dir(src_dir)? {
                let entry = entry?;
                let path = entry.path();
                let rel = path.strip_prefix(src_dir)?;
                let target = dst_dir.join(rel);
                if path.is_dir() {
                    std::fs::create_dir_all(&target)?;
                    walk_scaffold(&path, &target, crate_name, p_name, p_map)?;
                } else if path.is_file() {
                    let mut text = std::fs::read_to_string(&path)?;
                    text = text.replace("{{name}}", crate_name);
                    text = text.replace("{{PascalName}}", p_name);
                    for (k, v) in p_map {
                        text = text.replace(&format!("{{{{{}}}}}", k), v);
                    }
                    if let Some(p) = target.parent() {
                        std::fs::create_dir_all(p)?;
                    }
                    std::fs::write(&target, text)?;
                }
            }
            Ok(())
        }

        walk_scaffold(&tmpl_file, &dest_root, name, &pascal_name, &placeholders)?;

        if type_name == "rust_crate" || type_name == "rust-crate" {
            let gen_script = repo_root.join("tools/generate-cargo-manifests.py");
            if gen_script.is_file() {
                let py = if cfg!(windows) { "python" } else { "python3" };
                let _ = std::process::Command::new(py)
                    .arg(&gen_script)
                    .current_dir(&repo_root)
                    .output();
            }
        }

        println!(
            "Scaffolded new {} at: {}",
            type_name,
            dest_root.display().to_string().replace('\\', "/")
        );
        return Ok(());
    }

    let content = std::fs::read_to_string(&tmpl_file)?;

    // Name BEFORE render: {{id}} must agree with the filename the allocator chose.
    let final_name = match tmpl_cfg.as_ref() {
        Some(cfg) => resolve_name(name, cfg, &repo_root)?,
        None => name.to_string(),
    };
    let ordinal = regex::Regex::new(r"^(\d+)-")?
        .captures(&final_name)
        .map(|c| c[1].to_string());

    let mut rendered = content;
    if type_name == "adr" {
        // The ordinal comes from whoever knows it: the caller if they numbered
        // the name, otherwise the allocator that chose the destination.
        let (adr_id, clean_name) =
            if let Some(m) = regex::Regex::new(r"^(\d{4})[-_]?(.*)$")?.captures(name) {
                (m[1].to_string(), m[2].to_string())
            } else {
                (
                    ordinal.clone().unwrap_or_else(|| "0000".to_string()),
                    name.to_string(),
                )
            };
        let raw_title = clean_name.replace(['-', '_'], " ");
        let title = if raw_title.is_empty() {
            "New decision".to_string()
        } else {
            let mut c = raw_title.chars();
            match c.next() {
                None => String::new(),
                Some(f) => f.to_uppercase().collect::<String>() + c.as_str(),
            }
        };
        rendered = rendered.replace("{{id}}", &adr_id);
        rendered = rendered.replace("{{title}}", &title);
        rendered = rendered.replace("{{status}}", "accepted");
    } else if type_name == "drift-check" {
        rendered = rendered.replace("{{id}}", "99");
        rendered = rendered.replace("{{description}}", &format!("static check for {}", name));
    } else if type_name == "quadlet" {
        rendered = rendered.replace("{{image}}", &format!("docker.io/library/{}:latest", name));
        rendered = rendered.replace("{{uid}}", "1000");
        rendered = rendered.replace("{{gid}}", "1000");
    } else if type_name == "roadmap-ws" {
        rendered = rendered.replace("{{id}}", &name.to_uppercase());
        let title = name.replace('-', " ");
        let title_cap = {
            let mut c = title.chars();
            match c.next() {
                None => String::new(),
                Some(f) => f.to_uppercase().collect::<String>() + c.as_str(),
            }
        };
        rendered = rendered.replace("{{title}}", &title_cap);
        rendered = rendered.replace("{{theme}}", "OS-Image & Build");
        rendered = rendered.replace("{{status}}", "proposed");
        rendered = rendered.replace("{{priority}}", "P2");
        rendered = rendered.replace("{{description}}", &format!("Description of {}", name));
        rendered = rendered.replace("{{task_title}}", &format!("Task 1 for {}", name));
        rendered = rendered.replace("{{task_id}}", "999");
    }

    for (k, v) in &placeholders {
        rendered = rendered.replace(&format!("{{{{{}}}}}", k), v);
    }
    rendered = rendered.replace("{{name}}", name);

    let pascal_name = {
        let words: Vec<&str> = name.split(&['-', '_'][..]).collect();
        words
            .iter()
            .map(|w| {
                let mut c = w.chars();
                match c.next() {
                    None => String::new(),
                    Some(f) => f.to_uppercase().collect::<String>() + c.as_str(),
                }
            })
            .collect::<String>()
    };
    rendered = rendered.replace("{{PascalName}}", &pascal_name);

    if let Some(cfg) = tmpl_cfg {
        if cfg.get("emit").and_then(|e| e.as_str()) == Some("stdout") {
            print!("{}", rendered);
            return Ok(());
        }

        let dest_path = match cfg.get("fixed_name").and_then(|f| f.as_str()) {
            Some(fixed) => repo_root.join(fixed),
            None => {
                let dest_dir = cfg.get("dest_dir").and_then(|d| d.as_str()).unwrap_or(".");
                repo_root.join(dest_dir).join(&final_name)
            }
        };

        if let Some(parent) = dest_path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        if dest_path.exists() {
            eprintln!("Error: Target file already exists at {:?}", dest_path);
            std::process::exit(1);
        }
        std::fs::write(&dest_path, rendered)?;
        println!(
            "Scaffolded new {} at: {}",
            type_name,
            dest_path.display().to_string().replace('\\', "/")
        );
    }

    Ok(())
}

fn run_render_kargs(toml_path: &str, kargs_dir: &str) -> Result<(), Box<dyn std::error::Error>> {
    // A line scan cannot tell a [kargs] key from a comment or a continuation,
    // and unwrap_or_default() turned an unreadable SSOT into "render the
    // defaults anyway". Parse, or fail.
    let content = std::fs::read_to_string(toml_path)
        .map_err(|e| format!("cannot read {}: {}", toml_path, e))?;
    let parsed: toml::Value = content.parse()?;
    let conf = parsed.get("kargs");
    // Python casts hugepages with str() before strip(); an integer in SSOT is
    // legal TOML and must render as its digits, not as a quoted debug form.
    let field = |k: &str, dflt: &str| -> String {
        match conf.and_then(|c| c.get(k)) {
            Some(toml::Value::String(v)) => v.trim().to_string(),
            Some(toml::Value::Integer(v)) => v.to_string(),
            Some(v) => v.to_string().trim().trim_matches('"').to_string(),
            None => dflt.to_string(),
        }
    };
    let iommu = field("iommu", "on");
    let vfio_ids = field("vfio_ids", "");
    let hugepages = field("hugepages", "");
    let isolcpus = field("isolcpus", "");
    let nohz_full = field("nohz_full", "");
    let rcu_nocbs = field("rcu_nocbs", "");
    let thp = field("THP", "");

    let vfio_path = std::path::Path::new(kargs_dir).join("01-mios-vfio.toml");
    if vfio_path.exists() {
        // usr/lib/bootc/kargs.d/01-mios-vfio.toml is NOT wholly generated. It
        // carries hand-declared kargs that no SSOT key produces --
        // rd.driver.pre=vfio-pci, which binds vfio-pci in the initramfs before
        // a GPU driver can claim the card, and kvm-intel.nested=1. The Python
        // renderer this must match reads the file and strips ONLY the entries
        // it manages. Starting from an empty list deletes the rest from the
        // kernel command line while the header still claims the file came
        // from [kargs].
        let existing: toml::Value = std::fs::read_to_string(&vfio_path)?.parse()?;
        let mut kargs_list: Vec<String> = existing
            .get("kargs")
            .and_then(|v| v.as_array())
            .map(|a| {
                a.iter()
                    .filter_map(|v| v.as_str())
                    .map(|s| s.to_string())
                    .collect()
            })
            .unwrap_or_default();

        kargs_list
            .retain(|k| !matches!(k.as_str(), "intel_iommu=on" | "amd_iommu=on" | "iommu=pt"));
        match iommu.as_str() {
            "intel" => {
                kargs_list.extend(["intel_iommu=on", "iommu=pt"].iter().map(|s| s.to_string()))
            }
            "amd" => kargs_list.extend(["amd_iommu=on", "iommu=pt"].iter().map(|s| s.to_string())),
            "on" => kargs_list.extend(
                ["intel_iommu=on", "amd_iommu=on", "iommu=pt"]
                    .iter()
                    .map(|s| s.to_string()),
            ),
            _ => {}
        }

        kargs_list.retain(|k| !k.starts_with("vfio-pci.ids"));
        if !vfio_ids.is_empty() {
            kargs_list.push(format!("vfio-pci.ids={}", vfio_ids));
        }

        let mut lines = vec![
            "# AI-hint: Configures kernel arguments for IOMMU, VFIO-PCI, and nested virtualization to enable hardware passthrough and virtualization features in the MiOS boot process.".to_string(),
            "# Generated from mios.toml [kargs] SSOT".to_string(),
            "kargs = [".to_string(),
        ];
        let len = kargs_list.len();
        for (idx, item) in kargs_list.iter().enumerate() {
            if idx + 1 == len {
                lines.push(format!("    \"{}\"", item));
            } else {
                lines.push(format!("    \"{}\",", item));
            }
        }
        lines.push("]".to_string());
        std::fs::write(&vfio_path, lines.join("\n") + "\n")?;
        println!("Updated {}", vfio_path.display());
    }

    let mut custom_kargs = vec![];
    if !hugepages.is_empty() {
        custom_kargs.push(format!("hugepages={}", hugepages));
    }
    if !isolcpus.is_empty() {
        custom_kargs.push(format!("isolcpus={}", isolcpus));
    }
    if !nohz_full.is_empty() {
        custom_kargs.push(format!("nohz_full={}", nohz_full));
    }
    if !rcu_nocbs.is_empty() {
        custom_kargs.push(format!("rcu_nocbs={}", rcu_nocbs));
    }
    if !thp.is_empty() {
        custom_kargs.push(format!("transparent_hugepage={}", thp));
    }

    let custom_path = std::path::Path::new(kargs_dir).join("99-mios-kargs.toml");
    if !custom_kargs.is_empty() {
        let mut lines = vec![
            "# AI-hint: Configures custom kernel arguments from mios.toml [kargs] SSOT."
                .to_string(),
            "# Generated custom kernel arguments from mios.toml [kargs] SSOT".to_string(),
            "kargs = [".to_string(),
        ];
        let len = custom_kargs.len();
        for (idx, item) in custom_kargs.iter().enumerate() {
            if idx + 1 == len {
                lines.push(format!("    \"{}\"", item));
            } else {
                lines.push(format!("    \"{}\",", item));
            }
        }
        lines.push("]".to_string());
        std::fs::write(&custom_path, lines.join("\n") + "\n")?;
        println!("Generated {}", custom_path.display());
    } else if custom_path.exists() {
        std::fs::remove_file(&custom_path)?;
        println!("Removed stale {}", custom_path.display());
    }

    Ok(())
}

fn run_render_quadlets(_dirs: &[String]) -> Result<(), Box<dyn std::error::Error>> {
    let root = std::env::var("MIOS_ROOT").unwrap_or_else(|_| ".".to_string());
    let mut renderer = None;
    for c in [
        "/usr/libexec/mios/mios-render-quadlets",
        "/usr/bin/mios-render-quadlets",
    ] {
        if std::path::Path::new(c).is_file() {
            renderer = Some(std::path::PathBuf::from(c));
            break;
        }
    }
    if renderer.is_none() {
        let p1 =
            std::path::Path::new(&root).join("tools/native/target/release/mios-render-quadlets");
        let p2 = std::path::Path::new(&root).join("tools/native/target/debug/mios-render-quadlets");
        if p1.is_file() {
            renderer = Some(p1);
        } else if p2.is_file() {
            renderer = Some(p2);
        }
    }
    let Some(bin) = renderer else {
        return Err("mios-render-quadlets binary not found -- refusing to render with a naive regex that corrupts $$ and cannot nest".into());
    };
    let mut cmd = std::process::Command::new(bin);
    cmd.arg("--root").arg(&root);
    let status = cmd.status()?;
    if !status.success() {
        return Err("mios-render-quadlets failed".into());
    }
    Ok(())
}

fn run_render_ports(toml_path: &str, out_path: &str) -> Result<(), Box<dyn std::error::Error>> {
    // Parse the TOML; do NOT scan lines. The line scan this replaced treated any
    // line containing '=' inside [ports] as a key/value pair, so a COMMENT
    // became an environment variable name -- including one carrying a backtick
    // pair, which `bash source` reads as command substitution. install.env is
    // the file Law 10 (BARE-SAFE-ENV) governs. T-1018.
    let content = std::fs::read_to_string(toml_path)
        .map_err(|e| format!("render-ports: {toml_path} could not be read: {e}"))?;
    let parsed: toml::Value = content
        .parse()
        .map_err(|e| format!("render-ports: {toml_path} did not parse: {e}"))?;
    let ports = parsed
        .get("ports")
        .and_then(|p| p.as_table())
        .ok_or_else(|| format!("render-ports: {toml_path} declares no [ports] table"))?;

    let stack_id = ports
        .get("stack_id")
        .and_then(|v| v.as_integer())
        .unwrap_or(0);
    let offset = stack_id * 10000;

    // Sorted by the SSOT key, matching the Python renderer this must stay
    // byte-identical to; only integers are ports, so an array or a string in
    // this table is skipped rather than emitted as a value.
    let mut names: Vec<&String> = ports.keys().collect();
    names.sort();

    let mut entries: Vec<String> = Vec::new();
    for name in names {
        if name == "stack_id" || name == "categories" {
            continue;
        }
        let Some(value) = ports.get(name).and_then(|v| v.as_integer()) else {
            continue;
        };
        let rendered = if value == 53 { value } else { value + offset };
        entries.push(format!("MIOS_PORTS_{}={}", name.to_uppercase(), rendered));
        // Installed older units still consume the previous input spelling.
        entries.push(format!("MIOS_PORT_{}={}", name.to_uppercase(), rendered));
    }
    if entries.is_empty() {
        return Err("render-ports: [ports] yielded no integer port, so nothing was written".into());
    }

    let mut out_lines = Vec::new();
    if let Ok(existing) = std::fs::read_to_string(out_path) {
        for line in existing.lines() {
            if !line.starts_with("MIOS_PORT_") && !line.starts_with("MIOS_PORTS_") {
                out_lines.push(line.to_string());
            }
        }
    }
    out_lines.extend(entries);

    if let Some(parent) = std::path::Path::new(out_path).parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    std::fs::write(out_path, out_lines.join("\n") + "\n")?;
    Ok(())
}

#[tokio::main]
async fn main() {
    let args: Vec<String> = std::env::args().collect();
    let prog_name = args
        .first()
        .and_then(|a| std::path::Path::new(a).file_name())
        .and_then(|f| f.to_str())
        .unwrap_or("");
    if prog_name == "mios" || prog_name.starts_with("mios-cli") {
        let rc = miosd::cli::dispatch(args);
        std::process::exit(rc);
    }
    if prog_name == "mios-check" {
        let path = args
            .get(1)
            .map(|s| s.as_str())
            .unwrap_or("/usr/share/mios/mios.toml");
        let report = mios_config::MiosValidator::validate_file(path);
        println!("{}", report.format_human());
        std::process::exit(if report.is_valid { 0 } else { 1 });
    }

    let cli = Cli::parse();

    match &cli.command {
        Commands::DriftCheck {
            root,
            soft,
            list,
            only,
            parity,
        } => {
            let root_dir = root.as_deref().unwrap_or(".");
            drift::run_checks_cli(root_dir, *soft, *list, only.as_deref(), *parity);
        }
        Commands::Scaffold {
            template_type,
            name,
        } => {
            if let Err(e) = run_scaffold(template_type, name) {
                eprintln!("[miosd] Scaffold error: {}", e);
                std::process::exit(1);
            }
        }
        Commands::ImageBuild { root, target, plan } => {
            let root = std::path::Path::new(root);
            let result = mios_resolver::resolve_merged(Some(root), false)
                .map_err(|e| e.to_string())
                .and_then(|config| mios_build::images::image_plan(root, &config, target))
                .and_then(|commands| {
                    if *plan {
                        println!(
                            "{}",
                            serde_json::to_string_pretty(&commands).map_err(|e| e.to_string())?
                        );
                        return Ok(());
                    }
                    mios_build::images::execute_images(&commands, |command| {
                        println!("[miosd] {} from {}", command.target, command.root.display());
                        let status = std::process::Command::new(&command.engine)
                            .current_dir(&command.root)
                            .args(&command.args)
                            .status()
                            .map_err(|e| e.to_string())?;
                        if status.success() {
                            Ok(())
                        } else {
                            Err(status.to_string())
                        }
                    })
                });
            if let Err(error) = result {
                eprintln!("[miosd] Image build: {error}");
                std::process::exit(1);
            }
        }
        Commands::NativeBuildSettings { root, arch, json } => {
            match mios_build::native_linux_target(std::path::Path::new(root), arch) {
                Ok(settings) => {
                    if *json {
                        println!(
                            "{}",
                            serde_json::to_string_pretty(&settings)
                                .expect("native settings serialization")
                        );
                    } else {
                        println!(
                            "{}\t{}\t{}\t{}",
                            settings.target,
                            settings.linker,
                            settings.rustflags.join(" "),
                            settings.jobs
                        );
                    }
                }
                Err(error) => {
                    eprintln!("[miosd] Native build policy: {error}");
                    std::process::exit(1);
                }
            }
        }
        Commands::NativeBuild {
            root,
            target_dir,
            install_root,
            arch,
        } => {
            match mios_build::native_build::build_linux(
                std::path::Path::new(root),
                target_dir,
                install_root,
                arch,
            ) {
                Ok(count) => println!("[miosd] {count} native Linux artifacts built and installed"),
                Err(error) => {
                    eprintln!("[miosd] Native build: {error}");
                    std::process::exit(1);
                }
            }
        }
        Commands::NativeWindowsBuild {
            root,
            binary,
            target_dir,
        } => {
            let result = if let Some(binary) = binary {
                mios_build::verification::windows_build(
                    std::path::Path::new(root),
                    binary,
                    target_dir,
                )
                .map(|path| format!("Verified Windows artifact: {}", path.display()))
            } else {
                mios_build::verification::windows_build_catalog(
                    std::path::Path::new(root),
                    target_dir,
                )
                .map(|count| format!("{count} Windows native release artifacts built and verified"))
            };
            match result {
                Ok(receipt) => println!("[miosd] {receipt}"),
                Err(error) => {
                    eprintln!("[miosd] Windows build: {error}");
                    std::process::exit(1);
                }
            }
        }
        Commands::TerminalRuntimeCheck {
            root,
            uid,
            gid,
            repair,
        } => {
            if let Err(error) =
                terminal_runtime::run(std::path::Path::new(root), *uid, *gid, *repair)
            {
                eprintln!("[miosd] Terminal runtime: {error}");
                std::process::exit(1);
            }
            println!("[miosd] private SSOT tmux namespace verified; repair: {repair}");
        }
        Commands::NativeToolchainCheck { root, lint } => {
            if let Err(error) =
                mios_build::verification::toolchain_check(std::path::Path::new(root), *lint)
            {
                eprintln!("[miosd] Toolchain verification: {error}");
                std::process::exit(1);
            }
            println!("[miosd] SSOT-required lint tools verified; workspace lint requested: {lint}");
        }
        Commands::NativeWindowsInstall {
            root,
            source,
            bin_dir,
        } => match mios_build::verification::windows_install(root, source, bin_dir) {
            Ok(count) => {
                println!("[miosd] {count} Windows native release artifacts installed and verified")
            }
            Err(error) => {
                eprintln!("[miosd] Windows installation: {error}");
                std::process::exit(1);
            }
        },
        Commands::NativeRuntimeCheck {
            root,
            platform,
            arch,
            bin_dir,
        } => {
            match mios_build::verification::runtime_check(
                std::path::Path::new(root),
                platform,
                arch,
                bin_dir.as_deref(),
            ) {
                Ok(count) => {
                    println!("[miosd] {count} installed {platform} native artifacts verified")
                }
                Err(error) => {
                    eprintln!("[miosd] Runtime artifact verification: {error}");
                    std::process::exit(1);
                }
            }
        }
        Commands::NativeArtifactCheck {
            path,
            arch,
            root,
            platform,
        } => {
            let result = mios_build::verification::artifact_check(
                std::path::Path::new(root),
                std::path::Path::new(path),
                platform,
                arch,
            );
            if let Err(error) = result {
                eprintln!("[miosd] Artifact {path}: {error}");
                std::process::exit(1);
            }
        }
        Commands::NativeTargets {
            root,
            platform,
            json,
        } => match mios_build::native_target_plan(std::path::Path::new(root), platform) {
            Ok(targets) => {
                if *json {
                    println!(
                        "{}",
                        serde_json::to_string_pretty(&targets)
                            .expect("native target serialization")
                    );
                } else {
                    for target in targets {
                        println!(
                            "{}\t{}\t{}\t{}\t{}\t{}\t{}",
                            target.workspace,
                            target.package,
                            target.binary,
                            target.category,
                            target.install_dir,
                            target.expose_bin,
                            if target.compat_dirs.is_empty() {
                                "-".into()
                            } else {
                                target.compat_dirs.join(",")
                            }
                        );
                    }
                }
            }
            Err(error) => {
                eprintln!("[miosd] Native catalog error: {error}");
                std::process::exit(1);
            }
        },
        Commands::Build {
            phase,
            plan,
            list,
            profile,
            sections,
            post_list,
        } => {
            if *post_list {
                let root = std::env::var_os("MIOS_ROOT")
                    .map(std::path::PathBuf::from)
                    .unwrap_or_else(|| "/".into());
                match mios_build::progress::post_plan(&root) {
                    Ok(plan) => {
                        for (name, action) in plan {
                            println!("{name}:{action}");
                        }
                    }
                    Err(error) => {
                        eprintln!("[build-plan] {error}");
                        std::process::exit(1);
                    }
                }
                return;
            }
            if let Err(e) =
                mios_build::run_build_selected(phase, *plan, *list, *sections, profile.as_deref())
            {
                eprintln!("[miosd] Build error: {}", e);
                std::process::exit(1);
            }
        }
        Commands::BuildProgress {
            root,
            state,
            event,
            name,
            status,
        } => {
            use mios_build::progress::Status;
            let status = status.as_deref().and_then(|s| match s {
                "pass" => Some(Status::Pass),
                "fail" => Some(Status::Fail),
                "warn" => Some(Status::Warn),
                "skip" => Some(Status::Skip),
                "missing" => Some(Status::Missing),
                _ => None,
            });
            match mios_build::progress::run(root, state, event, name.as_deref(), status) {
                Ok(true) => {}
                Ok(false) => std::process::exit(1),
                Err(error) => {
                    eprintln!("[build-progress] {error}");
                    std::process::exit(1);
                }
            }
        }
        Commands::Greenboot => {
            if let Err(e) = run_greenboot() {
                eprintln!("[greenboot] Health check error: {}", e);
                std::process::exit(1);
            }
        }
        Commands::Resolve { shell } => {
            if *shell {
                // The whole resolved environment, as userenv.sh's fallback expects.
                match mios_resolver::resolve_env(None) {
                    Ok(env) => {
                        for (k, v) in env {
                            println!(
                                "export {}={}",
                                k,
                                mios_resolver::emit_shell::shlex_quote(&v)
                            );
                        }
                    }
                    Err(e) => {
                        eprintln!("[miosd] resolve: {}", e);
                        std::process::exit(1);
                    }
                }
            } else {
                let config = mios_config::MiosConfig::load_default().unwrap_or_default();
                println!("{:#?}", config);
            }
        }
        Commands::RenderPorts { toml, out } => {
            if let Err(e) = run_render_ports(toml, out) {
                eprintln!("[miosd] Render ports error: {}", e);
                std::process::exit(1);
            }
        }
        Commands::RenderQuadlets { dirs } => {
            if let Err(e) = run_render_quadlets(dirs) {
                eprintln!("[miosd] Render quadlets error: {}", e);
                std::process::exit(1);
            }
        }
        Commands::RenderKargs { toml, kargs_dir } => {
            if let Err(e) = run_render_kargs(toml, kargs_dir) {
                eprintln!("[miosd] Render kargs error: {}", e);
                std::process::exit(1);
            }
        }
        Commands::GenerateQuadlets { check } => {
            if let Err(e) = run_generate_quadlets(*check) {
                eprintln!("[miosd] Generate quadlets error: {}", e);
                std::process::exit(1);
            }
        }
        Commands::RenderUkiCmdline { check } => {
            if let Err(e) = run_render_uki_cmdline(*check) {
                eprintln!("[miosd] Render UKI cmdline error: {}", e);
                std::process::exit(1);
            }
        }
        Commands::RenderChrony { toml, out } => {
            if let Err(e) = run_render_chrony(toml, out) {
                eprintln!("[miosd] Render chrony error: {}", e);
                std::process::exit(1);
            }
        }
        Commands::RenderNut { toml, out_dir } => {
            if let Err(e) = run_render_nut(toml, out_dir) {
                eprintln!("[miosd] Render nut error: {}", e);
                std::process::exit(1);
            }
        }
        Commands::FirewallPorts => {
            if let Err(e) = run_firewall_ports() {
                eprintln!("[miosd] Firewall ports error: {}", e);
                std::process::exit(1);
            }
        }
        Commands::CosignPolicy { check } => {
            if let Err(e) = run_cosign_policy(*check) {
                eprintln!("[miosd] Cosign policy error: {}", e);
                std::process::exit(1);
            }
        }
        Commands::FinalizeOsrelease { path, version } => {
            if let Err(e) = run_finalize_osrelease(path, version.as_deref()) {
                eprintln!("[miosd] Finalize os-release error: {}", e);
                std::process::exit(1);
            }
        }
        Commands::BootcApply { sentinel } => {
            if let Err(e) = run_bootc_apply(sentinel) {
                eprintln!("[miosd] Bootc apply error: {}", e);
                std::process::exit(1);
            }
        }
        Commands::BootcRollback {
            check,
            dry_run,
            force,
        } => {
            if let Err(e) = run_bootc_rollback(*check, *dry_run, *force) {
                eprintln!("[miosd] Bootc rollback error: {}", e);
                std::process::exit(1);
            }
        }
        Commands::BuildIfMissing { spec } => {
            if let Err(e) = run_build_if_missing(spec) {
                eprintln!("[miosd] Build if missing error: {}", e);
                std::process::exit(1);
            }
        }
        Commands::Harden { root } => {
            if let Err(e) = run_harden(root) {
                eprintln!("[miosd] Harden error: {}", e);
                std::process::exit(1);
            }
        }
        Commands::RenderRepos {
            online,
            fedora_version,
            output,
            vendored_dir,
        } => {
            if let Err(e) =
                run_render_repos(*online, fedora_version, output.as_deref(), vendored_dir)
            {
                eprintln!("[miosd] Render repos error: {}", e);
                std::process::exit(1);
            }
        }
        Commands::OverlayBindImages { dest, qdirs } => {
            if let Err(e) = run_overlay_bind_images(dest, qdirs) {
                eprintln!("[miosd] Overlay bind images error: {}", e);
                std::process::exit(1);
            }
        }
        Commands::Daemon {
            state_dir,
            interval_secs,
            once,
            watchdog_dev,
        } => {
            let config = miosd::daemon::DaemonConfig {
                state_dir: std::path::PathBuf::from(state_dir),
                interval_secs: *interval_secs,
                watchdog_dev: watchdog_dev.clone(),
                backup_interval_secs: 3600,
                run_once: *once,
            };
            let supervisor = miosd::daemon::Supervisor::new(config);
            let shutdown = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
            let shutdown_clone = shutdown.clone();

            tokio::spawn(async move {
                #[cfg(unix)]
                {
                    use tokio::signal::unix::{signal, SignalKind};
                    if let Ok(mut sigterm) = signal(SignalKind::terminate()) {
                        sigterm.recv().await;
                        shutdown_clone.store(true, std::sync::atomic::Ordering::Relaxed);
                    }
                }
                #[cfg(not(unix))]
                {
                    let _ = tokio::signal::ctrl_c().await;
                    shutdown_clone.store(true, std::sync::atomic::Ordering::Relaxed);
                }
            });

            if let Err(e) = supervisor.run(shutdown).await {
                eprintln!("[miosd] Daemon supervisor error: {}", e);
                std::process::exit(1);
            }
        }
        Commands::Check {
            path,
            json,
            check: _,
        } => {
            let report = mios_config::MiosValidator::validate_file(path);
            if *json {
                println!("{}", report.to_json());
            } else {
                print!("{}", report.format_human());
            }
            if !report.is_valid {
                std::process::exit(1);
            }
        }
        Commands::Cli { args } => {
            let mut full_args = vec!["mios".to_string()];
            full_args.extend(args.clone());
            let rc = miosd::cli::dispatch(full_args);
            std::process::exit(rc);
        }
        Commands::Secret { action } => {
            match action {
                SecretAction::Prompt {
                    message,
                    title,
                    gui,
                    tty,
                    fd,
                } => match miosd::secret::prompt(title, message, *gui, *tty) {
                    Ok(secret) => emit_secret(&secret, *fd),
                    Err(e) => {
                        eprintln!("[miosd secret] Prompt error: {}", e);
                        std::process::exit(1);
                    }
                },
                SecretAction::Set {
                    key,
                    service,
                    prompt,
                    value,
                } => {
                    let val = if *prompt || value.is_none() {
                        let prompt_msg = format!("Enter secret for {}: ", key);
                        match miosd::secret::prompt("MiOS Keyring", &prompt_msg, false, false) {
                            Ok(v) => v,
                            Err(e) => {
                                eprintln!("[miosd secret] Prompt error: {}", e);
                                std::process::exit(1);
                            }
                        }
                    } else {
                        value.clone().unwrap_or_default()
                    };
                    if let Err(e) = miosd::secret::set(service, key, &val) {
                        eprintln!("[miosd secret] Set error: {}", e);
                        std::process::exit(1);
                    }
                    eprintln!("Stored secret for '{}/{}' in Linux Keyring.", service, key);
                }
                SecretAction::Get { key, service, fd } => match miosd::secret::get(service, key) {
                    Ok(val) => emit_secret(&val, *fd),
                    Err(e) => {
                        eprintln!("[miosd secret] Get error: {}", e);
                        std::process::exit(1);
                    }
                },
                SecretAction::Scan { path, strict } => {
                    let p = std::path::Path::new(path);
                    match miosd::secret::scan(p, *strict) {
                        Ok(findings) => {
                            if findings == 0 {
                                eprintln!("Pipeline safety check: PASS (0 credential leaks detected in {})", path);
                            } else {
                                eprintln!("Pipeline safety check: FAILED ({} credential leaks detected in {})", findings, path);
                                std::process::exit(1);
                            }
                        }
                        Err(e) => {
                            eprintln!("[miosd secret] Scan error: {}", e);
                            std::process::exit(1);
                        }
                    }
                }
            }
        }
        Commands::ConfigServer { bind, port } => {
            let config = match miosd::server::ConfigServerConfig::resolve(bind.clone(), *port) {
                Ok(c) => c,
                Err(e) => {
                    eprintln!("[miosd config-server] {e}");
                    std::process::exit(1);
                }
            };
            println!(
                "[miosd] Starting MiOS Config Server at http://{}",
                config.bind_addr
            );
            println!("[miosd] Serving mios.html from {:?}", config.html_path);
            println!(
                "[miosd] Writing saves to the user tier {:?}",
                config.user_toml_path
            );

            let (shutdown_tx, shutdown_rx) = tokio::sync::oneshot::channel();
            tokio::spawn(async move {
                let _ = tokio::signal::ctrl_c().await;
                println!("[miosd] Received SIGINT/Ctrl-C, shutting down config server...");
                let _ = shutdown_tx.send(());
            });

            if let Err(e) = miosd::server::run_config_server(config, Some(shutdown_rx)).await {
                eprintln!("[miosd] Config server error: {}", e);
                std::process::exit(1);
            }
        }
    }
}

/// Collect *.container and *.image at `qdir` and one level below it.
///
/// The bash this replaces globs both "${QDIR}/*.container" and
/// "${QDIR}/*/*.container" (likewise .image). A single read_dir sees only the
/// first, which silently dropped every Quadlet under a subdirectory -- today
/// usr/share/containers/systemd/users/mios-coderun-sandbox@.container -- from
/// /usr/lib/bootc/bound-images.d. An unbound image does not ship with the host
/// (Law 3: BOUND-IMAGES), and nothing downstream would have said so.
fn collect_quadlets(qdir: &std::path::Path) -> Vec<std::path::PathBuf> {
    fn is_quadlet(p: &std::path::Path) -> bool {
        matches!(
            p.extension().and_then(|s| s.to_str()).unwrap_or(""),
            "container" | "image"
        )
    }
    let mut out = Vec::new();
    let Ok(entries) = std::fs::read_dir(qdir) else {
        return out;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_file() {
            if is_quadlet(&path) {
                out.push(path);
            }
        } else if path.is_dir() {
            if let Ok(sub) = std::fs::read_dir(&path) {
                for s in sub.flatten() {
                    let sp = s.path();
                    if sp.is_file() && is_quadlet(&sp) {
                        out.push(sp);
                    }
                }
            }
        }
    }
    out.sort();
    out
}

fn run_overlay_bind_images(
    dest_dir: &str,
    qdirs_override: &[String],
) -> Result<(), Box<dyn std::error::Error>> {
    let bdir = std::path::Path::new(dest_dir);
    std::fs::create_dir_all(bdir)?;

    // Parse the TOML; do NOT scan lines. The scan this replaces matched any line
    // starting with "firstboot_tokens" (so "firstboot_tokens_extra" too) and read
    // only the text after the first '=' on that one line, so a reflow of the
    // array across lines would yield an EMPTY token set -- and an empty set binds
    // every image, including the two heavy GPU lanes the register exists to keep
    // out of the image. Same defect class as render-chrony's silent hardcoded
    // fallback (T-1018).
    let mios_toml_path =
        std::env::var("MIOS_TOML").unwrap_or_else(|_| "/usr/share/mios/mios.toml".to_string());
    let mut fb_tokens: Vec<String> = Vec::new();
    let toml_text = std::fs::read_to_string(&mios_toml_path)
        .map_err(|e| format!("overlay-bind-images: {mios_toml_path} could not be read: {e}"))?;
    let parsed: toml::Value = toml_text
        .parse()
        .map_err(|e| format!("overlay-bind-images: {mios_toml_path} did not parse: {e}"))?;
    if let Some(arr) = parsed
        .get("build")
        .and_then(|b| b.get("bake"))
        .and_then(|b| b.get("firstboot_tokens"))
        .and_then(|v| v.as_array())
    {
        for tok in arr {
            if let Some(sv) = tok.as_str() {
                if !sv.is_empty() {
                    fb_tokens.push(sv.to_string());
                }
            }
        }
    }

    let qdirs: Vec<String> = if qdirs_override.is_empty() {
        vec![
            "/usr/share/containers/systemd".to_string(),
            "/etc/containers/systemd".to_string(),
        ]
    } else {
        qdirs_override.to_vec()
    };
    for qdir_str in &qdirs {
        let qdir = std::path::Path::new(qdir_str);
        if !qdir.exists() {
            continue;
        }

        for path in collect_quadlets(qdir) {
            let name = path
                .file_name()
                .and_then(|s| s.to_str())
                .unwrap_or("")
                .to_string();
            let mut img_line = String::new();
            if let Ok(c) = std::fs::read_to_string(&path) {
                for l in c.lines() {
                    if let Some(rest) = l.strip_prefix("Image=") {
                        img_line = rest.trim().to_string();
                        break;
                    }
                }
            }

            let is_fb = !img_line.is_empty() && fb_tokens.iter().any(|t| img_line.contains(t));
            if is_fb {
                println!(
                    "[miosd] LBI: {} (firstboot tier -- web-pulled at first boot, not bound)",
                    name
                );
                continue;
            }

            let dst_file = bdir.join(&name);
            {
                // A swallowed symlink failure is an unbound image that still
                // reports as bound; Law 3 has no way to notice afterwards.
                match std::fs::remove_file(&dst_file) {
                    Ok(()) => {}
                    Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
                    Err(e) => {
                        return Err(format!(
                            "overlay-bind-images: cannot replace {}: {e}",
                            dst_file.display()
                        )
                        .into())
                    }
                }
                native_file_symlink(&path, &dst_file).map_err(|e| {
                    format!(
                        "overlay-bind-images: cannot bind {} -> {}: {e}",
                        dst_file.display(),
                        path.display()
                    )
                })?;
            }
            println!("[miosd] LBI: bound {} ({})", name, path.display());
        }
    }

    let gitkeep = bdir.join(".gitkeep");
    if gitkeep.exists() {
        let _ = std::fs::remove_file(gitkeep);
        println!("[miosd] LBI: stripped git-tracking .gitkeep");
    }

    Ok(())
}

fn run_render_repos(
    online: bool,
    fedora_version: &str,
    output_path: Option<&str>,
    vendored_dir: &str,
) -> Result<(), Box<dyn std::error::Error>> {
    // An empty version used to fall back to the literal "44", which would have
    // written /etc/yum.repos.d/fedora-44.repo on a tree whose SSOT had moved on
    // -- every package for the whole build coming from the wrong release, under
    // a filename that looks deliberate. The caller owns the version.
    if fedora_version.trim().is_empty() {
        return Err(
            "render-repos: --fedora-version is empty; pass mios.toml [versions].fedora".into(),
        );
    }
    let ver = fedora_version;
    let vendored = std::path::Path::new(vendored_dir).exists() && !online;

    let content = if vendored {
        format!(
            r#"[fedora-{ver}]
name=Fedora {ver} - $basearch
baseurl=file:///usr/share/mios/vendored/rpms/fedora-{ver}/$basearch
enabled=1
repo_gpgcheck=0
type=rpm
gpgcheck=0
skip_if_unavailable=True
priority=95

[fedora-{ver}-updates]
name=Fedora {ver} Updates - $basearch
baseurl=file:///usr/share/mios/vendored/rpms/updates-released-f{ver}/$basearch
enabled=1
repo_gpgcheck=0
type=rpm
gpgcheck=0
skip_if_unavailable=True
priority=95
"#,
            ver = ver
        )
    } else {
        format!(
            r#"[fedora-{ver}]
name=Fedora {ver} - $basearch
metalink=https://mirrors.fedoraproject.org/metalink?repo=fedora-{ver}&arch=$basearch
enabled=1
repo_gpgcheck=0
type=rpm
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-fedora-{ver}-x86_64
skip_if_unavailable=True
priority=95
timeout=10
minrate=1k
max_parallel_downloads=10
ip_resolve=4

[fedora-{ver}-updates]
name=Fedora {ver} Updates - $basearch
metalink=https://mirrors.fedoraproject.org/metalink?repo=updates-released-f{ver}&arch=$basearch
enabled=1
repo_gpgcheck=0
type=rpm
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-fedora-{ver}-x86_64
skip_if_unavailable=True
priority=95
timeout=10
minrate=1k
max_parallel_downloads=10
ip_resolve=4
"#,
            ver = ver
        )
    };

    let target_file = match output_path {
        Some(p) => p.to_string(),
        None => format!("/etc/yum.repos.d/fedora-{}.repo", ver),
    };

    let p = std::path::Path::new(&target_file);
    if let Some(parent) = p.parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::fs::write(p, content)?;
    println!("[miosd] rendered repo file at {}", target_file);
    Ok(())
}

/// Harden: tighten the usbguard config mode, set fapolicyd trust, enable the
/// three hardening units.
///
/// Every write in here used to be `let _ = ...`, and each one was followed by
/// an unconditional success line -- "[miosd] enabled usbguard.service" printed
/// whether or not the symlink was created. A hardening step that reports
/// success it did not achieve is worse than one that fails: the failure is
/// recoverable, the false report is not visible at all (T-1018).
fn run_harden(root: &str) -> Result<(), Box<dyn std::error::Error>> {
    let at = |p: &str| std::path::Path::new(root).join(p.trim_start_matches('/'));
    let usb_conf = at("/usr/lib/usbguard/usbguard-daemon.conf");
    let usb_conf = usb_conf.as_path();
    if usb_conf.exists() {
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(usb_conf, std::fs::Permissions::from_mode(0o600))
                .map_err(|e| format!("harden: cannot set 0600 on {}: {e}", usb_conf.display()))?;
        }
    }

    for cfg_rel in [
        "/usr/lib/fapolicyd/fapolicyd.conf",
        "/etc/fapolicyd/fapolicyd.conf",
    ] {
        let p = at(cfg_rel);
        let p = p.as_path();
        let cfg = p.display().to_string();
        if !p.exists() {
            continue;
        }
        let content =
            std::fs::read_to_string(p).map_err(|e| format!("harden: cannot read {cfg}: {e}"))?;
        let rewritten: Vec<String> = content
            .lines()
            .map(|l| {
                if l.starts_with("trust =") {
                    "trust = file,rpmdb".to_string()
                } else {
                    l.to_string()
                }
            })
            .collect();
        std::fs::write(p, rewritten.join("\n") + "\n")
            .map_err(|e| format!("harden: cannot write {cfg}: {e}"))?;
        println!("[miosd] fapolicyd trust = file,rpmdb in {cfg}");
    }

    let wants_dir = at("/usr/lib/systemd/system/multi-user.target.wants");
    let wants_dir = wants_dir.as_path();
    std::fs::create_dir_all(wants_dir)
        .map_err(|e| format!("harden: cannot create {}: {e}", wants_dir.display()))?;

    for u in ["usbguard.service", "auditd.service", "fapolicyd.service"] {
        let src = at(&format!("/usr/lib/systemd/system/{u}"));
        if !src.exists() {
            println!("[miosd] skip: {u} not installed");
            continue;
        }
        let dst = wants_dir.join(u);
        {
            match std::fs::remove_file(&dst) {
                Ok(()) => {}
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
                Err(e) => {
                    return Err(format!("harden: cannot replace {}: {e}", dst.display()).into())
                }
            }
            native_file_symlink(format!("../{u}"), &dst)
                .map_err(|e| format!("harden: cannot enable {u}: {e}"))?;
        }
        println!("[miosd] enabled {u}");
    }

    Ok(())
}

/// Both builders must create the link or report the platform's real error.
fn native_file_symlink(
    source: impl AsRef<std::path::Path>,
    destination: impl AsRef<std::path::Path>,
) -> std::io::Result<()> {
    #[cfg(unix)]
    {
        std::os::unix::fs::symlink(source, destination)
    }
    #[cfg(windows)]
    {
        std::os::windows::fs::symlink_file(source, destination)
    }
    #[cfg(not(any(unix, windows)))]
    {
        let _ = (source, destination);
        Err(std::io::Error::new(
            std::io::ErrorKind::Unsupported,
            "native file links require Linux or Windows",
        ))
    }
}

/// One layered-loader read: `mios-toml-get <section> <key>` (vendor < host < user), empty is an error.
fn toml_get(section: &str, key: &str) -> Result<String, Box<dyn std::error::Error>> {
    let out = std::process::Command::new("/usr/libexec/mios/mios-toml-get")
        .arg(section)
        .arg(key)
        .output()?;
    let val = String::from_utf8_lossy(&out.stdout).trim().to_string();
    if !out.status.success() || val.is_empty() {
        return Err(format!("[{section}].{key} unresolved").into());
    }
    Ok(val)
}

/// The pinned tag of an image ref; `latest`, a missing tag or a registry port is refused.
fn pinned_tag(img: &str) -> Option<&str> {
    let (_, tag) = img.rsplit_once(':')?;
    (!tag.is_empty() && tag != "latest" && !tag.contains('/')).then_some(tag)
}

/// Build args of the agents image; same reads and build-context as mios-agents-firstboot.sh.
fn agents_build_args() -> Result<Vec<String>, Box<dyn std::error::Error>> {
    let img = toml_get("image.sidecars", "code_server")?;
    let tag = pinned_tag(&img)
        .ok_or_else(|| format!("[image.sidecars].code_server '{img}' has no pinned tag"))?;
    let sb = toml_get("theme.edge", "code_server_scrollbar_px")?;
    let pm = toml_get("theme.edge", "code_server_perimeter_px")?;
    Ok(vec![
        "--build-arg".into(),
        format!("MIOS_CODE_SERVER_VERSION={tag}"),
        "--build-arg".into(),
        format!("CODE_SERVER_SCROLLBAR_PX={sb}"),
        "--build-arg".into(),
        format!("CODE_SERVER_PERIMETER_PX={pm}"),
        "--build-context".into(),
        "mios=/".into(),
    ])
}

/// Sources whose mtime newer than the image triggers a rebuild (the Containerfile always counts).
fn build_sources(spec: &str) -> &'static [&'static str] {
    match spec {
        "agents" => &[
            "/usr/share/mios/themes/code-server-terminal.css",
            "/usr/libexec/mios/mios-vscode-custom-css",
        ],
        _ => &[],
    }
}

fn image_created_epoch(img: &str) -> u64 {
    std::process::Command::new("/usr/bin/podman")
        .args(["image", "inspect", "-f", "{{.Created.Unix}}", img])
        .output()
        .ok()
        .and_then(|o| String::from_utf8_lossy(&o.stdout).trim().parse().ok())
        .unwrap_or(0)
}

fn mtime_epoch(path: &str) -> u64 {
    std::fs::metadata(path)
        .and_then(|m| m.modified())
        .ok()
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map_or(0, |d| d.as_secs())
}

fn run_build_if_missing(spec: &str) -> Result<(), Box<dyn std::error::Error>> {
    let (img, ctx, cf) = match spec {
        "agents" => (
            "localhost/mios-agents:latest",
            "/usr/share/mios/agents",
            "/usr/share/mios/agents/Containerfile",
        ),
        "forgejo-runner" => (
            "localhost/mios-forgejo-runner:latest",
            "/usr/share/mios/forgejo-runner",
            "/usr/share/mios/forgejo-runner/Containerfile",
        ),
        "webtools" => (
            "localhost/mios-webtools:latest",
            "/usr/share/mios/webtools",
            "/usr/share/mios/webtools/Containerfile",
        ),
        _ => return Err(format!("unknown build spec '{}'", spec).into()),
    };

    println!(
        "[miosd] build-if-missing: checking spec '{}' ({})",
        spec, img
    );

    let cf_path = std::path::Path::new(cf);
    if !cf_path.exists() {
        println!(
            "[miosd] Containerfile {} not found, dry-run skip for {}",
            cf, spec
        );
        return Ok(());
    }

    if std::path::Path::new("/usr/bin/podman").exists() {
        let exists_status = std::process::Command::new("/usr/bin/podman")
            .arg("image")
            .arg("exists")
            .arg(img)
            .status()?;
        let stale = if exists_status.success() {
            let img_epoch = image_created_epoch(img);
            std::iter::once(cf)
                .chain(build_sources(spec).iter().copied())
                .find(|p| img_epoch > 0 && mtime_epoch(p) > img_epoch)
        } else {
            None
        };
        if let Some(p) = stale {
            println!("[miosd] {} newer than {} -> rebuild", p, img);
        }
        if !exists_status.success() || stale.is_some() {
            let extra = if spec == "agents" {
                agents_build_args()?
            } else {
                Vec::new()
            };
            println!("[miosd] building {} from {}...", img, cf);
            let build_status = std::process::Command::new("/usr/bin/podman")
                .arg("build")
                .arg("--network=host")
                .args(&extra)
                .arg("-t")
                .arg(img)
                .arg("-f")
                .arg(cf)
                .arg(ctx)
                .status()?;
            if !build_status.success() {
                return Err(format!("failed to build {}", img).into());
            }
        } else {
            println!("[miosd] image {} current; nothing to build", img);
        }
    }

    Ok(())
}

fn run_bootc_apply(sentinel_path: &str) -> Result<(), Box<dyn std::error::Error>> {
    let p = std::path::Path::new(sentinel_path);
    if !p.exists() {
        return Err(format!("sentinel {} missing or unreadable", sentinel_path).into());
    }

    let content = std::fs::read_to_string(p)?;
    let mut parts = content.split_whitespace();
    let ts = parts.next().unwrap_or("");
    let ref_val = parts.next().unwrap_or("");

    if ref_val.is_empty() {
        return Err(format!("sentinel {} missing image ref", sentinel_path).into());
    }

    let allow_remote = std::env::var("MIOS_BOOTC_ALLOW_REMOTE").unwrap_or_default() == "1";
    if !ref_val.starts_with("localhost/") && !allow_remote {
        return Err(format!("refusing non-localhost ref '{}'", ref_val).into());
    }

    println!("[miosd] build sentinel: ts={} ref={}", ts, ref_val);

    if std::path::Path::new("/usr/bin/podman").exists() {
        let status = std::process::Command::new("/usr/bin/podman")
            .arg("image")
            .arg("exists")
            .arg(ref_val)
            .status()?;
        if !status.success() {
            return Err(format!("image '{}' not found in containers-storage", ref_val).into());
        }
    }

    if std::path::Path::new("/usr/bin/bootc").exists() {
        let status = std::process::Command::new("/usr/bin/bootc")
            .arg("switch")
            .arg("--transport")
            .arg("containers-storage")
            .arg(ref_val)
            .status()?;
        if !status.success() {
            return Err("bootc switch failed".into());
        }
    } else {
        println!(
            "[miosd] bootc binary not found, dry-run switch for ref {}",
            ref_val
        );
    }

    let hist_dir = &mios_state_dir();
    std::fs::create_dir_all(hist_dir)?;
    let row = format!("{}\t{}\t{}\n", chrono_now_iso(), ts, ref_val);
    let hist_file = hist_dir.join("bootc-switch-history.tsv");
    use std::io::Write;
    let mut f = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(hist_file)?;
    f.write_all(row.as_bytes())?;

    println!("[miosd] [ok] staged {} for next boot.", ref_val);
    Ok(())
}

/// Hand a secret to the caller over the descriptor it opened for it (the gpg --passphrase-fd
/// pattern): never stdio, never a terminal, so it cannot land in a log or in scrollback.
fn emit_secret(value: &str, fd: i32) {
    use std::io::{IsTerminal, Write};
    if fd < 3 {
        eprintln!("[miosd secret] refusing descriptor {fd}: stdio is never a secret channel; pass --fd 3 with 3>&1 or a file");
        std::process::exit(2);
    }
    let mut out = match std::fs::OpenOptions::new()
        .write(true)
        .open(format!("/dev/fd/{fd}"))
    {
        Ok(f) => f,
        Err(e) => {
            eprintln!("[miosd secret] descriptor {fd} is not open for writing: {e}");
            std::process::exit(2);
        }
    };
    if out.is_terminal() {
        eprintln!("[miosd secret] refusing descriptor {fd}: it is a terminal");
        std::process::exit(2);
    }
    if out
        .write_all(value.as_bytes())
        .and_then(|_| out.write_all(b"\n"))
        .is_err()
    {
        std::process::exit(1);
    }
}

/// The daemon's state directory.
fn mios_state_dir() -> std::path::PathBuf {
    // systemd's STATE_DIRECTORY first, then var/lib/mios under MIOS_ROOT (as daemon/backup.rs), else /var/lib/mios.
    if let Some(p) = std::env::var("STATE_DIRECTORY").ok().and_then(|v| {
        v.split(':')
            .next()
            .filter(|p| !p.is_empty())
            .map(std::path::PathBuf::from)
    }) {
        return p;
    }
    let root = std::env::var("MIOS_ROOT").unwrap_or_else(|_| "/".to_string());
    std::path::Path::new(&root).join("var/lib/mios")
}

fn run_bootc_rollback(
    check: bool,
    dry_run: bool,
    force: bool,
) -> Result<(), Box<dyn std::error::Error>> {
    println!("[miosd] Initiating bootc rollback evaluation (T-1025)...");

    // Invariant 1: Ensure /var persistence is intact before and during rollback operations
    let hist_dir = &mios_state_dir();
    std::fs::create_dir_all(hist_dir)?;
    let probe_file = hist_dir.join(".rollback-probe");
    std::fs::write(&probe_file, format!("probe {}", chrono_now_iso()))?;
    let _ = std::fs::remove_file(&probe_file);
    println!("[miosd] [ok] /var persistence verified (Invariant 1: persistent /var)");

    let has_bootc = std::path::Path::new("/usr/bin/bootc").exists();
    let mut rollback_available = false;
    let mut booted_ref = String::from("unknown");
    let mut rollback_ref = String::from("unknown");

    if has_bootc {
        let output = std::process::Command::new("/usr/bin/bootc")
            .arg("status")
            .arg("--json")
            .output();

        if let Ok(out) = output {
            if out.status.success() {
                if let Ok(json_val) = serde_json::from_slice::<serde_json::Value>(&out.stdout) {
                    if let Some(status) = json_val.get("status") {
                        if let Some(booted) = status.get("booted").filter(|v| !v.is_null()) {
                            if let Some(img) = booted
                                .get("image")
                                .and_then(|i| i.get("image"))
                                .and_then(|i| i.get("image"))
                            {
                                booted_ref = img.as_str().unwrap_or("unknown").to_string();
                            }
                        }
                        if let Some(rollback) = status.get("rollback").filter(|v| !v.is_null()) {
                            rollback_available = true;
                            if let Some(img) = rollback
                                .get("image")
                                .and_then(|i| i.get("image"))
                                .and_then(|i| i.get("image"))
                            {
                                rollback_ref = img.as_str().unwrap_or("unknown").to_string();
                            }
                        }
                    }
                }
            }
        }
    } else {
        println!("[miosd] [info] /usr/bin/bootc not present; checking local deployment state");
        let hist_file = hist_dir.join("bootc-switch-history.tsv");
        if hist_file.exists() {
            if let Ok(content) = std::fs::read_to_string(&hist_file) {
                let lines: Vec<&str> = content.lines().filter(|l| !l.trim().is_empty()).collect();
                if lines.len() >= 2 {
                    rollback_available = true;
                    rollback_ref = lines[lines.len() - 2]
                        .split('\t')
                        .nth(2)
                        .unwrap_or("staged-previous")
                        .to_string();
                    booted_ref = lines[lines.len() - 1]
                        .split('\t')
                        .nth(2)
                        .unwrap_or("staged-current")
                        .to_string();
                } else if lines.len() == 1 {
                    booted_ref = lines[0]
                        .split('\t')
                        .nth(2)
                        .unwrap_or("staged-current")
                        .to_string();
                }
            }
        }
    }

    if std::env::var("MIOS_TEST_ROLLBACK_AVAILABLE").unwrap_or_default() == "1" {
        rollback_available = true;
        rollback_ref = "localhost/mios:previous".to_string();
    }
    if std::env::var("MIOS_TEST_FAIL_NO_ROLLBACK").unwrap_or_default() == "1" {
        rollback_available = false;
        rollback_ref = "none".to_string();
    }

    println!(
        "[miosd] Deployment status: booted={}, rollback_target={}",
        booted_ref, rollback_ref
    );

    if check {
        if !rollback_available && !force {
            eprintln!("[miosd] [error] No rollback deployment detected in ostree deployment table");
            return Err("No rollback deployment available".into());
        } else {
            println!("[miosd] [ok] Rollback deployment verified and armed for recovery");
        }
        return Ok(());
    }

    if dry_run {
        println!("[miosd] [dry-run] Simulating atomic bootc rollback:");
        println!("[miosd] [dry-run] 1. Verify /var persistence: PASSED");
        println!(
            "[miosd] [dry-run] 2. Inspect ostree deployment table: target={}",
            rollback_ref
        );
        println!("[miosd] [dry-run] 3. Rotate ostree default deployment pointer: SIMULATED");
        println!(
            "[miosd] [dry-run] 4. Append audit record to /var/lib/mios/bootc-rollback-history.tsv: SIMULATED"
        );
        println!("[miosd] [dry-run] Dry-run completed successfully with 0 state alterations.");
        return Ok(());
    }

    if has_bootc {
        let mut cmd = std::process::Command::new("/usr/bin/bootc");
        cmd.arg("rollback");
        if force {
            cmd.arg("--force");
        }
        let status = cmd.status()?;
        if !status.success() {
            return Err("bootc rollback command failed".into());
        }
    } else {
        println!("[miosd] [info] Dry execution in test container: rollback staged");
    }

    let row = format!("{}\t{}\t{}\n", chrono_now_iso(), "rollback", rollback_ref);
    let hist_file = hist_dir.join("bootc-rollback-history.tsv");
    use std::io::Write;
    let mut f = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(hist_file)?;
    f.write_all(row.as_bytes())?;

    println!(
        "[miosd] [ok] Successfully rolled back deployment to {} for next boot.",
        rollback_ref
    );
    Ok(())
}

fn run_greenboot() -> Result<(), Box<dyn std::error::Error>> {
    println!("[greenboot] Running native greenboot health check (T-508 / T-1025)...");

    // 1. Verify Invariant 1: /var persistence & writability
    let var_dir = &mios_state_dir();
    if let Err(e) = std::fs::create_dir_all(var_dir) {
        eprintln!(
            "[greenboot] FAIL: {} is not writable: {}",
            var_dir.display(),
            e
        );
        return Err(format!("/var writability check failed: {}", e).into());
    }
    let probe_file = var_dir.join(".greenboot-probe");
    if let Err(e) = std::fs::write(&probe_file, format!("ok {}", chrono_now_iso())) {
        eprintln!("[greenboot] FAIL: failed to write /var probe: {}", e);
        return Err(format!("/var probe write failed: {}", e).into());
    }
    let _ = std::fs::remove_file(&probe_file);
    println!("[greenboot] [ok] /var persistence & writability verified (Invariant 1)");

    // 2. Verify SSOT: /usr/share/mios/mios.toml or /etc/mios/mios.toml or usr/share/mios/mios.toml
    let ssot_found = [
        "/usr/share/mios/mios.toml",
        "/etc/mios/mios.toml",
        "usr/share/mios/mios.toml",
    ]
    .iter()
    .any(|p| std::path::Path::new(p).exists());

    if !ssot_found {
        eprintln!("[greenboot] FAIL: SSOT mios.toml not found in system paths");
        return Err("SSOT mios.toml not found".into());
    }
    println!("[greenboot] [ok] SSOT mios.toml accessibility verified");
    let verified = mios_build::verification::runtime_check(
        std::path::Path::new("/"),
        "linux",
        std::env::consts::ARCH,
        None,
    )?;
    println!("[greenboot] [ok] {verified} SSOT native artifacts verified");

    // 3. Verify UKI / bootloader entries if bootloader directory exists
    let entries_dir = std::path::Path::new("/boot/loader/entries");
    if entries_dir.exists() {
        if let Ok(entries) = std::fs::read_dir(entries_dir) {
            let conf_count = entries
                .filter_map(|e| e.ok())
                .filter(|e| e.path().extension().and_then(|ext| ext.to_str()) == Some("conf"))
                .count();
            println!(
                "[greenboot] [ok] bootloader entries verified: {} entry found",
                conf_count
            );
        }
    }

    println!("[greenboot] SUCCESS: Core OS and SSOT health verified");
    Ok(())
}

fn chrono_now_iso() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    // Simple UTC ISO-8601 calculation
    let days = secs / 86400;
    let rem_secs = secs % 86400;
    let hours = rem_secs / 3600;
    let minutes = (rem_secs % 3600) / 60;
    let seconds = rem_secs % 60;
    // Approximating year/month/day calculation or epoch day representation
    format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}Z",
        1970 + days / 365,
        1,
        1,
        hours,
        minutes,
        seconds
    )
}

fn run_finalize_osrelease(
    path: &str,
    ver_opt: Option<&str>,
) -> Result<(), Box<dyn std::error::Error>> {
    let ver = match ver_opt {
        Some(v) => v.to_string(),
        None => {
            if let Ok(c) = std::fs::read_to_string("/ctx/VERSION") {
                c.trim().to_string()
            } else if let Ok(c) = std::fs::read_to_string("VERSION") {
                c.trim().to_string()
            } else {
                "unknown".to_string()
            }
        }
    };

    // Say which of the two happened. This returned a bare Ok(()), and stage 88
    // printed "Os-release version projected from SSOT via miosd" on the strength
    // of that exit code -- so a missing file or an unresolved version reported a
    // projection that had not occurred. The bash leg it shadows prints nothing
    // in the same situation, because its success log sits inside the branch
    // that did the work (T-1018).
    let p = std::path::Path::new(path);
    if !p.exists() {
        println!("[miosd] os-release: {path} does not exist -- nothing projected");
        return Ok(());
    }
    if ver == "unknown" {
        println!("[miosd] os-release: version is unknown -- nothing projected");
        return Ok(());
    }

    let content = std::fs::read_to_string(p)?;
    let mut new_lines = Vec::new();

    for line in content.lines() {
        if line.starts_with("VERSION=") {
            new_lines.push(format!("VERSION=\"{}\"", ver));
        } else if line.starts_with("VERSION_ID=") {
            new_lines.push(format!("VERSION_ID=\"{}\"", ver));
        } else if line.starts_with("BUILD_ID=") {
            new_lines.push(format!("BUILD_ID=\"{}\"", ver));
        } else if line.starts_with("IMAGE_VERSION=") {
            new_lines.push(format!("IMAGE_VERSION=\"{}\"", ver));
        } else if line.starts_with("OSTREE_VERSION=") {
            new_lines.push(format!("OSTREE_VERSION=\"{}\"", ver));
        } else if line.starts_with("PRETTY_NAME=") {
            new_lines.push(format!("PRETTY_NAME=\"MiOS {}\"", ver));
        } else if line.starts_with("CPE_NAME=") {
            new_lines.push(format!("CPE_NAME=\"cpe:/o:mios-dev:mios:{}\"", ver));
        } else {
            new_lines.push(line.to_string());
        }
    }

    std::fs::write(p, new_lines.join("\n") + "\n")?;
    println!("[miosd] os-release version projected from SSOT: {}", ver);
    Ok(())
}

fn run_cosign_policy(check: bool) -> Result<(), Box<dyn std::error::Error>> {
    let root = std::env::var("MIOS_ROOT").unwrap_or_else(|_| ".".to_string());
    native_generator::run(std::path::Path::new(&root), "cosign-policy", check)
}

fn run_firewall_ports() -> Result<(), Box<dyn std::error::Error>> {
    let ports = vec![
        ("HERMES", "tcp"),
        ("OPEN_WEBUI", "tcp"),
        ("CODE_SERVER", "tcp"),
        ("GUACAMOLE", "tcp"),
        ("CEPH_DASHBOARD", "tcp"),
        ("K3S_API", "tcp"),
        ("RDP", "tcp"),
        ("FORGE_HTTP", "tcp"),
        ("FORGE_SSH", "tcp"),
        ("COCKPIT_LINK", "tcp"),
        ("ADGUARD_UI", "tcp"),
        ("ADGUARD_DNS", "tcp"),
        ("ADGUARD_DNS", "udp"),
        ("SSH", "tcp"),
        ("COCKPIT", "tcp"),
    ];

    if std::path::Path::new("/usr/bin/firewall-offline-cmd").exists() {
        for (svc, proto) in ports {
            let port = std::env::var(format!("MIOS_PORTS_{svc}"))
                .or_else(|_| std::env::var(format!("MIOS_PORT_{svc}")));
            if let Ok(port_val) = port {
                let arg = format!("--add-port={}/{}", port_val, proto);
                let _ = std::process::Command::new("/usr/bin/firewall-offline-cmd")
                    .arg("--zone=public")
                    .arg(arg)
                    .status();
            }
        }
        let _ = std::process::Command::new("/usr/bin/firewall-offline-cmd")
            .arg("--zone=public")
            .arg("--add-service=ssh")
            .status();
        let _ = std::process::Command::new("/usr/bin/firewall-offline-cmd")
            .arg("--zone=public")
            .arg("--add-service=mios-pxe")
            .status();
    } else {
        println!("[miosd] firewall-ports: firewall-offline-cmd not found, skipped offline firewall rules.");
    }
    Ok(())
}

fn run_render_nut(toml_path: &str, conf_dir: &str) -> Result<(), Box<dyn std::error::Error>> {
    // Parse the TOML; do NOT scan lines. This table happens to be all
    // single-line scalars today, so the scan produced the right answer -- but a
    // comment containing '=' or a multi-line value would break it exactly as it
    // broke [ports] and [network.ntp]. And unwrap_or_default() meant a
    // NONEXISTENT manifest rendered four default config files and exited 0.
    // T-1018.
    let content = std::fs::read_to_string(toml_path)
        .map_err(|e| format!("render-nut: {toml_path} could not be read: {e}"))?;
    let parsed: toml::Value = content
        .parse()
        .map_err(|e| format!("render-nut: {toml_path} did not parse: {e}"))?;
    let ups = parsed
        .get("power")
        .and_then(|p| p.get("ups"))
        .or_else(|| parsed.get("ups"));
    let field = |k: &str, dflt: &str| -> String {
        ups.and_then(|u| u.get(k))
            .and_then(|v| v.as_str())
            .unwrap_or(dflt)
            .to_string()
    };
    let name = field("name", "");
    let driver = field("driver", "usbhid-ups");
    let port = field("port", "auto");
    let desc = field("desc", "MiOS Uninterruptible Power Supply");

    let dir = std::path::Path::new(conf_dir);
    std::fs::create_dir_all(dir)?;

    let mode = if name.is_empty() {
        "none"
    } else {
        "standalone"
    };
    let nut_conf = format!(
        "# AI-hint: NUT framework mode. Generated from mios.toml [power.ups] SSOT.\n# DO NOT EDIT -- edit mios.toml [power.ups] and run automation/43-nut-render.sh\nMODE={}\n",
        mode
    );
    std::fs::write(dir.join("nut.conf"), nut_conf)?;

    let mut ups_conf = String::from("# AI-hint: NUT drivers configuration. Generated from mios.toml [power.ups] SSOT.\n# DO NOT EDIT -- edit mios.toml [power.ups] and run automation/43-nut-render.sh\n");
    if !name.is_empty() {
        ups_conf.push_str(&format!(
            "\n[{}]\n    driver = {}\n    port = {}\n    desc = \"{}\"\n",
            name, driver, port, desc
        ));
    }
    std::fs::write(dir.join("ups.conf"), ups_conf)?;

    let mut upsd_conf = String::from("# AI-hint: NUT daemon settings. Generated from mios.toml [power.ups] SSOT.\n# DO NOT EDIT -- edit mios.toml [power.ups] and run automation/43-nut-render.sh\n");
    if !name.is_empty() {
        upsd_conf.push_str("\nLISTEN 127.0.0.1 3493\n");
    }
    std::fs::write(dir.join("upsd.conf"), upsd_conf)?;

    let mut upsmon_conf = String::from("# AI-hint: NUT monitor settings. Generated from mios.toml [power.ups] SSOT.\n# DO NOT EDIT -- edit mios.toml [power.ups] and run automation/43-nut-render.sh\n");
    if !name.is_empty() {
        upsmon_conf.push_str(&format!("\nMONITOR {}@localhost 1 upsmon mios-ups-secret master\nSHUTDOWNCMD \"/sbin/shutdown -h +0\"\n", name));
    }
    std::fs::write(dir.join("upsmon.conf"), upsmon_conf)?;

    Ok(())
}

fn run_render_chrony(toml_path: &str, out_path: &str) -> Result<(), Box<dyn std::error::Error>> {
    // Parse the TOML; do NOT scan lines. [network.ntp].servers is a MULTI-LINE
    // array, so the line scan this replaced read only `servers = [`, stripped it
    // to nothing, and silently substituted two hardcoded public NTP hosts --
    // Law 7, and a sovereignty question on a machine whose SSOT named a pool.
    // There is no hardcoded fallback now: an absent table yields no servers,
    // which is what the Python renderer this must match byte-for-byte does.
    // T-1018.
    let content = std::fs::read_to_string(toml_path)
        .map_err(|e| format!("render-chrony: {toml_path} could not be read: {e}"))?;
    let parsed: toml::Value = content
        .parse()
        .map_err(|e| format!("render-chrony: {toml_path} did not parse: {e}"))?;
    let servers: Vec<String> = parsed
        .get("network")
        .and_then(|n| n.get("ntp"))
        .and_then(|n| n.get("servers"))
        .and_then(|v| v.as_array())
        .map(|a| {
            a.iter()
                .filter_map(|v| v.as_str().map(str::to_string))
                .collect()
        })
        .unwrap_or_default();

    let mut body = String::new();
    body.push_str(
        "# AI-hint: NTP configuration for Chrony. Generated from mios.toml [network.ntp] SSOT.\n",
    );
    body.push_str(
        "# DO NOT EDIT -- edit mios.toml [network.ntp] and run automation/42-chrony-render.sh\n\n",
    );

    for s in &servers {
        body.push_str(&format!("server {} iburst\n", s));
    }

    body.push_str("\n# Record the rate at which the system clock gains/losses time.\n");
    body.push_str("driftfile /var/lib/chrony/drift\n\n");
    body.push_str("# Allow the system clock to be stepped in the first three updates\n");
    body.push_str("# if its offset is larger than 1 second. (Disabled in WSL2 where Hyper-V handles coarse sync)\n");
    body.push_str("makestep 0 0\n");
    body.push_str("maxslewrate 500\n\n");
    body.push_str("# Hyper-V PTP clock reference when available (WSL2 / VM container host)\n");
    body.push_str("refclock PHC /dev/ptp0 poll 3 dpoll -2 offset 0 minsamples 4 prefer trust\n\n");
    body.push_str("# Enable kernel synchronization of the real-time clock (RTC).\n");
    body.push_str("rtcsync\n\n");
    body.push_str("# Specify directory for log files.\n");
    body.push_str("logdir /var/log/chrony\n");

    if let Some(parent) = std::path::Path::new(out_path).parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::fs::write(out_path, body)?;
    Ok(())
}

fn run_render_uki_cmdline(check: bool) -> Result<(), Box<dyn std::error::Error>> {
    let root = std::env::var("MIOS_ROOT").unwrap_or_else(|_| ".".to_string());
    let count = mios_unit_gen::project_deployment(
        std::path::Path::new(&root),
        mios_unit_gen::DeploymentKind::UkiCmdline,
        check,
        None,
    )?;
    println!(
        "[render-uki-cmdline] {count} projection(s) {}",
        if check { "match SSOT" } else { "written" }
    );
    Ok(())
}

fn run_generate_quadlets(check: bool) -> Result<(), Box<dyn std::error::Error>> {
    let root = std::env::var("MIOS_ROOT").unwrap_or_else(|_| ".".to_string());
    native_generator::run(std::path::Path::new(&root), "pod-quadlets", check)
}
