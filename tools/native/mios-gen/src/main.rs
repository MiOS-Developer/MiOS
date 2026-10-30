// AI-hint: Main entrypoint for mios-gen -- the unified SSOT projector and code generator binary (ADR-0021 gen category).
// AI-related: usr/share/mios/mios.toml, tools/native/Cargo.toml, docs/design/doc-rust-static-port.md, usr/share/doc/mios/adr/0021-rust-static-binary-consolidation.md

#![forbid(unsafe_code)]

use clap::{Parser, Subcommand};
use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::ExitCode;

mod agent_interaction;
mod ai;
mod btop_theme;
mod display_scale;
mod hypr3d_spawn;
mod docs;
mod greeter;
mod grub;
use render::desktop_boot;
mod image;
mod indexes;
mod metal_vs_hosted;
mod pod_quadlets;
mod global_bindings;
mod nested_session;
mod nested_devices;
mod session_scale;
mod render;
mod sync;
mod terminal;
mod tmux_os;

use ai::{ai_manifest, pipe_boundaries};
use docs::{standardize_docs, sync_wiki};
use image::{bib_configs, cargo_manifests};
use indexes::{adr_index, gate_index, pipeline_index, roadmap_index};
use render::{render_desktop, render_globals, render_manpages, render_ports, render_tuned};
use terminal::{fastfetch, tmux_theme};

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
    /// Render the complete native Linux/Windows dashboard from live layered SSOT.
    Dashboard {
        #[arg(long)]
        root: Option<PathBuf>,
        #[arg(long)]
        resolved_stdin: bool,
        #[arg(long)]
        width: Option<usize>,
        #[arg(long)]
        no_probe: bool,
        #[arg(long)]
        facts: Option<PathBuf>,
    },
    /// Compare or apply the SSOT-declared bootstrap mirror, preserving unowned keys.
    BootstrapSync {
        #[arg(long)]
        root: Option<PathBuf>,
        #[arg(long)]
        bootstrap: Option<PathBuf>,
        #[arg(long, conflicts_with = "apply")]
        check: bool,
        #[arg(long)]
        apply: bool,
    },
    /// Render a native projection in a private tracked-byte snapshot and print capped diffs.
    ProjectionEvidence {
        #[arg(long)]
        root: Option<PathBuf>,
        #[arg(long, value_parser = ["cargo-manifests", "gate-index", "bib-configs"])]
        generator: String,
        #[arg(long = "target", required = true)]
        targets: Vec<String>,
    },
    /// Project the complete canonical name registry and tracked consumer census.
    NamesRegistry {
        #[arg(long)]
        root: Option<PathBuf>,
        /// Verify both committed projections without changing either artifact.
        #[arg(long)]
        check: bool,
    },
    /// Regenerate every declared SSOT projection in dependency order.
    Sync {
        #[arg(long)]
        root: Option<PathBuf>,
        /// Validate every prerequisite and print the plan without changing files or the index.
        #[arg(long)]
        plan: bool,
    },
    /// Audit every SSOT variable's canonical name, aliases and collisions (no values).
    Names {
        #[arg(long)]
        root: Option<PathBuf>,
    },
    /// Build the git-native legacy archive of every dropped file from [legibility.archive] (ADR-0030 Decision 7).
    Archive {
        #[arg(long)]
        root: Option<PathBuf>,
        /// Compare the archive with the recomputed dropped set by path, mode and content; write nothing.
        #[arg(long)]
        check: bool,
    },
    /// Print the TERM/COLORTERM an SSH client must attach to MiOS-Tmux with ([theme.tmux].remote_truecolor_terms):
    /// nothing when the client keeps its own (local consoles, unlisted or already-truecolor TERMs).
    #[command(name = "tmux-client-env")]
    TmuxClientEnv {
        #[arg(long)]
        root: Option<PathBuf>,
    },
    /// Print the MiOS-Tmux Apps and TUIs menu as NUL-separated `display-menu` argv ([[terminal.tuis]]).
    #[command(name = "tmux-menu")]
    TmuxMenu {
        /// Menu to build (only `apps` is dynamic; the static menus are generated command aliases)
        which: String,
        #[arg(long)]
        root: Option<PathBuf>,
        /// 1 for a Linux-console client (argv_vt, vt_ok filter, single borders)
        #[arg(long, default_value = "0")]
        console: String,
        /// Client height in rows; a menu that does not fit pages by section
        #[arg(long, default_value_t = 24)]
        rows: u32,
        #[arg(long)]
        section: Option<String>,
    },
    /// Print a launchable TUI's NUL-separated spec (mode, label, width, height, hold, argv); exit 3 when hidden or not installed.
    #[command(name = "tui-spec")]
    TuiSpec {
        id: String,
        #[arg(long)]
        root: Option<PathBuf>,
        #[arg(long, default_value = "0")]
        console: String,
    },
    /// MiOS-Tmux executor behind mios-terminal (menu apps | app ID | float shell|lift|attach|reap | session-cycle next|prev | new).
    #[command(name = "tmux-os")]
    TmuxOs {
        #[arg(long)]
        root: Option<PathBuf>,
        #[arg(trailing_var_arg = true, allow_hyphen_values = true, required = true)]
        args: Vec<String>,
    },
    /// Render the complete Windows host tmux configuration from layered SSOT.
    RenderHostTmux {
        #[arg(long)]
        root: Option<PathBuf>,
        /// Caller has verified the active client uses the configured Nerd font.
        #[arg(long)]
        font_verified: bool,
    },
    /// Emit the shared native Linux/Windows terminal policy as nine validated lines.
    TerminalConfig {
        #[arg(long)]
        root: Option<PathBuf>,
        #[arg(long, default_value = "")]
        action: String,
        #[arg(long)]
        no_default: bool,
    },
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
        /// Compare the generated rules without writing files.
        #[arg(long)]
        check: bool,
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

    /// Project the Linux virtual-console RGB palette from layered SSOT.
    #[command(name = "render-console-theme")]
    RenderConsoleTheme {
        #[arg(long)]
        root: Option<PathBuf>,
        #[arg(long)]
        check: bool,
        #[arg(long)]
        out: Option<PathBuf>,
    },
    /// Emit palette escapes for the current terminal or Linux virtual console.
    #[command(name = "terminal-colors")]
    TerminalColors {
        #[arg(long)]
        root: Option<PathBuf>,
        #[arg(long)]
        term: String,
    },

    /// Build Hyprglass with the installed compositor's exact SDK and Lua ABI.
    #[command(name = "build-hyprglass")]
    BuildHyprglass {
        #[arg(long)]
        root: Option<PathBuf>,
        #[arg(long)]
        srpm: Option<PathBuf>,
    },

    /// Build the compositor plugin [desktop.hyprland.<name>] with the same SDK and ABI hold.
    #[command(name = "build-hyprland-plugin")]
    BuildHyprlandPlugin {
        #[arg(long)]
        name: String,
        #[arg(long)]
        root: Option<PathBuf>,
        #[arg(long)]
        srpm: Option<PathBuf>,
    },

    /// Launch the canonical MiOS desktop with the selected GPU hardware profile.
    #[command(name = "hyprland-session")]
    HyprlandSession {
        #[arg(long)]
        root: Option<PathBuf>,
        /// Select a named hardware profile without changing desktop configuration.
        #[arg(long)]
        hardware_profile: Option<String>,
        /// Report device selection without starting a compositor.
        #[arg(long)]
        dry_run: bool,
    },

    /// Run an isolated MiOS desktop session: a parent PiP/window, a whole output, the remote display or a Hypr3D room window.
    #[command(name = "nested-session")]
    NestedSession {
        #[arg(long)] root: Option<PathBuf>,
        #[arg(long)] dry_run: bool,
        /// Launch target ([desktop.nested].launch_layout): pip, window, side-by-side, output, remote or hypr3d.
        #[arg(long="target",visible_alias="layout",value_parser=["pip","window","side-by-side","output","remote","hypr3d"])] layout: Option<String>,
        /// Parent output for the output target (required there) or an explicit remote display output.
        #[arg(long)] output: Option<String>,
        /// Seat label (a-z, 0-9, '-'): the session's identity, tmux namespace suffix and [desktop.nested.seats.<seat>] preset.
        #[arg(long)] seat: Option<String>,
        /// Display name of the session's user (default: the seat label).
        #[arg(long)] user: Option<String>,
        /// Assign an input device (repeatable): /dev/input/eventN or a by-id/by-path link, name:<evdev name>, or virtual-pad.
        #[arg(long="device")] device: Vec<String>,
        /// Assign no device, overriding the seat preset and [desktop.nested.devices].default.
        #[arg(long,conflicts_with="device")] no_devices: bool,
        /// Print the resolved launch (target, identity, resolved devices) as JSON and exit without starting anything.
        #[arg(long)] plan: bool,
        /// List the host's input devices with their kind and current session assignment (JSON).
        #[arg(long)] list_devices: bool,
        /// List live nested sessions from the registry (JSON).
        #[arg(long)] list_sessions: bool,
    },

    /// Print the Super+Escape overlay rows (JSON [{keys,label}]) generated from the binding tables; writes nothing.
    #[command(name = "keymap-overlay")]
    KeymapOverlay {
        #[arg(long)] root: Option<PathBuf>,
        /// Read the merged SSOT as JSON from this file ("-" = stdin) instead of resolving --root's layers.
        #[arg(long)] from_json: Option<PathBuf>,
    },

    /// Scale the whole session on the focused output (or zoom a focused nested session): up, down or reset.
    #[command(name = "session-scale")]
    SessionScale {
        #[arg(long)] root: Option<PathBuf>,
        #[arg(value_parser=["up","down","reset","+","-","0"])] direction: String,
    },

    /// Load and toggle the live 3D engine through compositor IPC.
    #[command(name = "hyprland-3d-toggle")]
    Hyprland3dToggle,

    /// Hypr3D Retina window opening ([desktop.hyprland.hypr3d.spawn]): per output, the window_scale,
    /// spawn_distance, spawn box and fit the 3D toggle sends (a window opens at its flat-desktop size, one
    /// texel per output pixel), as JSON. Outputs from --monitors (a `hyprctl -j monitors` file) or the
    /// running compositor; [desktop.mobile] verdicts from --mobile or $XDG_RUNTIME_DIR/mios-mobile-outputs.json.
    #[command(name = "hyprland-3d-spawn")]
    Hyprland3dSpawn {
        /// SSOT root (layered vendor/host/user); default $MIOS_ROOT or /
        #[arg(long)]
        root: Option<PathBuf>,
        /// `hyprctl -j monitors` JSON file (default: ask the compositor)
        #[arg(long)]
        monitors: Option<PathBuf>,
        /// [desktop.mobile] verdicts file (default: $XDG_RUNTIME_DIR/mios-mobile-outputs.json)
        #[arg(long)]
        mobile: Option<PathBuf>,
        /// Only this output
        #[arg(long)]
        output: Option<String>,
        /// Hypr3D render.scale for the legibility floor (default: the SSOT global)
        #[arg(long = "render-scale")]
        render_scale: Option<f64>,
        /// A window size WxH (logical px) to place on each output; repeatable
        #[arg(long = "window")]
        window: Vec<String>,
    },

    /// Run the login chooser with the GPU profile selected at boot.
    #[command(name = "hyprland-greeter")]
    HyprlandGreeter {
        #[arg(long, value_parser = ["glass", "tuigreet"])]
        kind: Option<String>,
        #[arg(long)]
        dry_run: bool,
    },

    /// Select the matching managed GRUB entry; reboot when GPU ownership changes.
    #[command(name = "hyprland-boot-profile")]
    HyprlandBootProfile {
        #[arg(long, value_parser = ["igpu", "dgpu"])]
        hardware_profile: String,
        /// Configure the next boot without immediately rebooting.
        #[arg(long)]
        prepare_only: bool,
        #[arg(long)]
        dry_run: bool,
    },

    /// Global display scale policy ([desktop.scaling]) for one output: scale Hyprland accepts,
    /// logical size and mobile flag as one JSON line. Contract: docs in display_scale.rs.
    #[command(name = "display-scale")]
    DisplayScale {
        /// SSOT root (layered vendor/host/user); default $MIOS_ROOT or /
        #[arg(long)]
        root: Option<PathBuf>,
        /// Mode width in pixels (unrotated, as Hyprland reports it)
        #[arg(long)]
        width: i64,
        /// Mode height in pixels (unrotated)
        #[arg(long)]
        height: i64,
        /// EDID physical size WxH in mm (0x0 = unknown)
        #[arg(long = "phys-mm")]
        phys_mm: Option<String>,
        /// Client DPI hint (RDP: from the xrdp journal)
        #[arg(long)]
        dpi: Option<f64>,
        /// Hyprland transform 0..7 (odd = rotated 90/270)
        #[arg(long, default_value_t = 0)]
        transform: i64,
        /// The output is a remote display (RDP virtual output)
        #[arg(long)]
        remote: bool,
        /// Output name: an operator-saved scale for it in the SSOT makes it explicit
        #[arg(long)]
        output: Option<String>,
        /// Explicit scale (overrides --output lookup); never auto-scaled
        #[arg(long = "explicit-scale")]
        explicit_scale: Option<f64>,
        /// Main display logical rect X,Y,W,H: adds "position" centred under its bottom edge
        #[arg(long = "attach-below")]
        attach_below: Option<String>,
    },

    /// Project the current native Hyprland Lua configuration from SSOT.
    #[command(name = "render-hyprland")]
    RenderHyprland {
        #[arg(long)]
        root: Option<PathBuf>,
        #[arg(long)]
        check: bool,
        #[arg(long)]
        runtime: bool,
        /// Project separate hardware boot choices from this existing BLS type-1 entry.
        #[arg(long)]
        boot_entry: Option<PathBuf>,
    },

    /// Renders usr/lib/tuned/profiles/<name>/tuned.conf from mios.toml [host.tuning.profiles]
    #[command(name = "render-tuned")]
    RenderTuned {
        /// Repository root directory
        #[arg(long)]
        root: Option<PathBuf>,

        /// Check mode: report drift without writing
        #[arg(long)]
        check: bool,
    },

    /// Projects [agent_cli.interaction] values into agent CLI settings files under --home (validates only without it)
    #[command(name = "render-agent-interaction")]
    RenderAgentInteraction {
        /// Repository root directory
        #[arg(long)]
        root: Option<PathBuf>,

        /// Home directory whose client files receive the declared values; without it the SSOT table is only validated
        #[arg(long)]
        home: Option<PathBuf>,

        /// Read the layered SSOT (vendor < host < user), as an agent launch does
        #[arg(long)]
        runtime: bool,

        /// Report the client files that would change and exit 1, writing nothing
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

        /// Output directory override (defaults to writing under root)
        #[arg(long, hide = true)]
        out_dir: Option<PathBuf>,

        /// List mode: list relative paths of all rendered manual pages
        #[arg(long)]
        list: bool,
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

        /// Project layered (vendor < host < user) tmux.conf + mios.omp.json into a private DIRECTORY
        #[arg(long, value_name = "DIRECTORY", conflicts_with_all = ["check", "check_fixture", "write_fixture", "out"])]
        runtime: Option<PathBuf>,

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

    /// Projects the GRUB boot menu from [boot.grub] and [boot.grub.theme]: the /etc/default/grub managed block,
    /// the static recovery entry, the MiOS theme, grubenv (boot the last booted entry) and a BLS menu that holds
    /// only the managed GPU entries. Prints the projection unless --check, --stage or --apply is given.
    #[command(name = "render-grub")]
    RenderGrub {
        /// Root whose vendor+host SSOT layers are read (default: the live system)
        #[arg(long)]
        root: Option<PathBuf>,
        /// Filesystem whose /etc and /boot are projected; grub.cfg is regenerated only when this is "/"
        #[arg(long, default_value = "/")]
        sysroot: PathBuf,
        /// Exit 1 when a projected file, grubenv value or the BLS menu drifts from the SSOT
        #[arg(long, conflicts_with_all = ["stage", "apply"])]
        check: bool,
        /// Write everything and generate /boot/grub2/grub.cfg.mios-new, keeping the current grub.cfg
        #[arg(long, conflicts_with = "apply")]
        stage: bool,
        /// Write everything and install the regenerated /boot/grub2/grub.cfg (root)
        #[arg(long)]
        apply: bool,
        /// kernel-install event: first re-render the managed GPU entries from the newest installed kernel
        #[arg(long, value_parser = ["add", "remove"], requires = "apply")]
        kernel_event: Option<String>,
        /// Kernel version passed by kernel-install (logged and validated)
        #[arg(long, requires = "kernel_event")]
        kernel_version: Option<String>,
    },

    /// Projects greetd's config.toml from [desktop.greeter]: the MiOS glass Quickshell greeter
    /// (usr/libexec/mios/mios-greeter-session, tuigreet fallback) or plain tuigreet.
    /// Prints to stdout unless --out is given; never writes /etc implicitly.
    #[command(name = "render-greetd", aliases = ["greetd"])]
    RenderGreetd {
        /// Repository root whose vendor+host SSOT layers are read
        #[arg(long)]
        root: Option<PathBuf>,

        /// Filesystem the config will run on; launcher, greeter and cage are probed there
        #[arg(long, default_value = "/")]
        sysroot: PathBuf,

        /// Atomically write the projection to this path (mode/owner of an existing file kept)
        #[arg(long, conflicts_with = "check")]
        out: Option<PathBuf>,

        /// Compare this file with the projection; exit 1 on drift
        #[arg(long)]
        check: Option<PathBuf>,
    },

    /// Renders Fastfetch system banner JSONC configuration from host/AI metadata
    #[command(name = "render-fastfetch", aliases = ["fastfetch", "fastfetch-gen"])]
    RenderFastfetch {
        /// Repository root directory
        #[arg(long)]
        root: Option<PathBuf>,

        /// Check mode: verify target configuration matches projection without modifying it
        #[arg(long)]
        check: bool,

        /// Output path for config.jsonc file
        #[arg(long, aliases = ["output", "out"])]
        out: Option<PathBuf>,

        /// Fastfetch logo display type (small, auto, none, raw)
        #[arg(long, default_value = "small")]
        logo_type: String,

        /// Deterministic mock execution for testing and CI
        #[arg(long)]
        mock: bool,

        /// Simulate execution without writing files
        #[arg(long)]
        dry_run: bool,

        /// Generate configuration (alias for default execution)
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
    let vendor = mios_resolver::layers::resolve_tier_dirs(Some(root)).0;
    if !vendor.is_file() {
        return Err((format!("{} not found", vendor.display()), 1));
    }
    let target_path = root.join(mios_resolver::sigstore::ARTIFACT);
    // An in-tree projection: vendor tier only, never this host's /etc or $HOME
    // layers. The shared renderer refuses an invalid requirement type and any
    // policy under which a MiOS-owned image is accepted without a MiOS-anchored
    // signature, so an unsound policy is never written.
    let rendered = mios_resolver::sigstore::render_root(root)
        .map_err(|e| (format!("[security.sigstore]: {e}"), 1))?
        .json;

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
    if fs::read_to_string(&target_path).ok().as_deref() != Some(rendered.as_str()) {
        greeter::write_atomic(&target_path, &rendered)
            .map_err(|e| (format!("Failed to write {}: {e}", target_path.display()), 1))?;
    }

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

fn run_egress_firewall(root: &Path, check: bool) -> Result<(), (String, i32)> {
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

    if check {
        let current = fs::read(&out_path).map_err(|e| {
            (
                format!(
                    "egress-firewall: cannot compare {}: {e}",
                    out_path.display()
                ),
                1,
            )
        })?;
        if current != ruleset.as_bytes() {
            return Err((
                format!(
                    "egress-firewall: {} is stale -- run mios-gen egress-firewall",
                    out_path.display()
                ),
                1,
            ));
        }
        println!("[egress-fw] {} matches SSOT", out_path.display());
        return Ok(());
    }

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
        Commands::Hyprland3dSpawn { root, monitors, mobile, output, render_scale, window } => {
            let run = || -> Result<String, String> {
                let root = root.or_else(|| env::var_os("MIOS_ROOT").filter(|v| !v.is_empty()).map(PathBuf::from))
                    .unwrap_or_else(|| PathBuf::from("/"));
                let data = crate::terminal::tmux_theme::tmux_runtime::load_layered(&root)?;
                let text = match &monitors {
                    Some(path) => std::fs::read_to_string(path).map_err(|e| format!("{}: {e}", path.display()))?,
                    None => {
                        let out = std::process::Command::new("/usr/bin/hyprctl").args(["-j", "monitors"]).output()
                            .map_err(|e| format!("hyprctl: {e}"))?;
                        if !out.status.success() {
                            return Err("hyprctl -j monitors failed (no compositor? pass --monitors)".into());
                        }
                        String::from_utf8_lossy(&out.stdout).into_owned()
                    }
                };
                let monitors: serde_json::Value = serde_json::from_str(&text).map_err(|e| format!("monitors JSON: {e}"))?;
                let mobile_path = mobile.clone().or_else(|| env::var_os("XDG_RUNTIME_DIR")
                    .map(|r| PathBuf::from(r).join("mios-mobile-outputs.json")));
                let published: Option<serde_json::Value> = match (&mobile, mobile_path) {
                    (Some(path), _) => Some(serde_json::from_str(&std::fs::read_to_string(path)
                        .map_err(|e| format!("{}: {e}", path.display()))?).map_err(|e| format!("mobile JSON: {e}"))?),
                    (None, Some(path)) => std::fs::read_to_string(path).ok().and_then(|t| serde_json::from_str(&t).ok()),
                    (None, None) => None,
                };
                let windows = window.iter().map(|w| {
                    let (a, b) = w.split_once('x').ok_or_else(|| format!("--window {w}: expected WxH"))?;
                    let parse = |v: &str| v.parse::<f64>().ok().filter(|f| f.is_finite() && *f >= 1.0 && *f <= 32768.0);
                    parse(a).zip(parse(b)).ok_or_else(|| format!("--window {w}: expected WxH in 1..=32768"))
                }).collect::<Result<Vec<_>, _>>()?;
                if let Some(r) = render_scale {
                    if !(1.0..=4.0).contains(&r) {
                        return Err("--render-scale must be in 1..=4".into());
                    }
                }
                hypr3d_spawn::run_cli(&data, &monitors, published.as_ref(), output.as_deref(), render_scale, &windows)
            };
            return match run() {
                Ok(text) => {
                    println!("{text}");
                    ExitCode::SUCCESS
                }
                Err(error) => {
                    eprintln!("[hyprland-3d-spawn] {error}");
                    ExitCode::from(2)
                }
            };
        }
        Commands::DisplayScale {
            root,
            width,
            height,
            phys_mm,
            dpi,
            transform,
            remote,
            output,
            explicit_scale,
            attach_below,
        } => {
            return match display_scale::run_cli(
                root.or_else(|| env::var_os("MIOS_ROOT").filter(|v| !v.is_empty()).map(PathBuf::from)).unwrap_or_else(|| PathBuf::from("/")),
                display_scale::CliArgs { width, height, phys_mm, dpi, transform, remote, output, explicit_scale, attach_below },
                cli.format == "json",
            ) {
                Ok(text) => {
                    println!("{text}");
                    ExitCode::SUCCESS
                }
                Err(error) => {
                    eprintln!("[display-scale] {error}");
                    ExitCode::from(2)
                }
            };
        }
        Commands::Dashboard {
            root,
            resolved_stdin,
            width,
            no_probe,
            facts,
        } => {
            return match dashboard::run(
                &resolve_root(root),
                resolved_stdin,
                width,
                no_probe,
                cli.format == "json",
                facts.as_deref(),
            ) {
                Ok(output) => {
                    println!("{output}");
                    ExitCode::SUCCESS
                }
                Err(error) => {
                    eprintln!("[dashboard] {error}");
                    ExitCode::FAILURE
                }
            };
        }
        Commands::ProjectionEvidence {
            root,
            generator,
            targets,
        } => {
            let root = resolve_root(root);
            return match projection_evidence::run(&root, &generator, &targets) {
                Ok(()) => ExitCode::SUCCESS,
                Err(error) => {
                    eprintln!("[98-drift-checks][diff] evidence failed: {error}");
                    ExitCode::FAILURE
                }
            };
        }
        Commands::BootstrapSync {
            root,
            bootstrap,
            check: _,
            apply,
        } => {
            let root = resolve_root(root);
            let boot = bootstrap
                .or_else(|| env::var_os("MIOS_BOOTSTRAP_ROOT").map(PathBuf::from))
                .unwrap_or_else(|| root.parent().unwrap_or(&root).join("mios-bootstrap"));
            return match mios_gen::bootstrap_sync::run(&root, &boot, apply) {
                Ok(report) => {
                    println!("[sync-bootstrap] {report}");
                    ExitCode::SUCCESS
                }
                Err(error) => {
                    eprintln!("[sync-bootstrap] {error}");
                    ExitCode::FAILURE
                }
            };
        }
        Commands::NamesRegistry { root, check } => {
            let root = resolve_root(root);
            let result = if check {
                mios_gen::names_registry::check(&root)
            } else {
                mios_gen::names_registry::run(&root)
            };
            return match result {
                Ok(()) => ExitCode::SUCCESS,
                Err(error) => {
                    eprintln!("[mios-gen names-registry] {error}");
                    ExitCode::FAILURE
                }
            };
        }
        Commands::Sync { root, plan } => {
            let root = resolve_root(root);
            return match sync::run(&root, plan) {
                Ok(()) => ExitCode::SUCCESS,
                Err(error) => {
                    eprintln!("[mios-gen sync] {error}");
                    ExitCode::FAILURE
                }
            };
        }
        Commands::Archive { root, check } => {
            let root = resolve_root(root);
            return match archive::run(&root, check) {
                Ok(summary) => {
                    println!("[archive] {summary}");
                    ExitCode::SUCCESS
                }
                Err(archive::Failure::Violations(findings)) => {
                    for finding in findings {
                        println!("[archive] {finding}");
                    }
                    ExitCode::from(archive::EXIT_VIOLATIONS)
                }
                Err(archive::Failure::CannotRun(why)) => {
                    println!("[archive] cannot_run: {why}");
                    ExitCode::from(archive::EXIT_CANNOT_RUN)
                }
            };
        }
        Commands::Names { root } => {
            let root = resolve_root(root);
            let result = mios_resolver::resolve_merged(Some(&root), false)
                .map_err(|error| error.to_string())
                .and_then(|merged| mios_resolver::names::registry(&merged))
                .and_then(|registry| {
                    serde_json::to_string_pretty(&registry).map_err(|e| e.to_string())
                });
            return match result {
                Ok(output) => {
                    println!("{output}");
                    ExitCode::SUCCESS
                }
                Err(error) => {
                    eprintln!("[mios-gen names] {error}");
                    ExitCode::FAILURE
                }
            };
        }
        Commands::TmuxClientEnv { root } => {
            let root = resolve_root(root);
            let result = terminal::tmux_theme::tmux_runtime::load_layered(&root)
                .and_then(|merged| tmux_theme::TmuxThemeEngine::from_toml(&merged, None, None));
            return match result {
                Ok(engine) => {
                    let term = env::var("TERM").unwrap_or_default();
                    let mut dirs: Vec<PathBuf> = Vec::new();
                    if let Some(d) = env::var_os("TERMINFO") {
                        dirs.push(PathBuf::from(d));
                    }
                    if let Some(h) = env::var_os("HOME") {
                        dirs.push(PathBuf::from(h).join(".terminfo"));
                    }
                    if let Some(list) = env::var_os("TERMINFO_DIRS") {
                        dirs.extend(env::split_paths(&list).filter(|d| !d.as_os_str().is_empty()));
                    }
                    for d in ["/etc/terminfo", "/usr/share/terminfo", "/usr/lib/terminfo", "/lib/terminfo"] {
                        dirs.push(PathBuf::from(d));
                    }
                    if let Some((term, colorterm)) =
                        engine.remote_client_env(&term, tmux_theme::is_remote_terminal())
                    {
                        // tmux refuses a client whose TERM has no terminfo: never trade a working TERM for that.
                        if mios_service_core::tmux_theme::terminfo_exists(&term, &dirs) {
                            println!("TERM={term}\nCOLORTERM={colorterm}");
                        }
                    }
                    ExitCode::SUCCESS
                }
                Err(error) => {
                    eprintln!("[tmux-client-env] {error}");
                    ExitCode::FAILURE
                }
            };
        }
        Commands::TmuxMenu { which, root, console, rows, section } => {
            let root = resolve_root(root);
            let result = (|| -> Result<Option<Vec<String>>, String> {
                if which != "apps" {
                    return Err("tmux-menu: only the apps menu is dynamic".into());
                }
                let console = match console.as_str() { "1" => true, "0" => false, _ => return Err("--console must be 0 or 1".into()) };
                let merged = mios_resolver::resolve_merged(Some(&root), false).map_err(|e| e.to_string())?;
                tmux_os::menu_argv(&merged, console, rows, section.as_deref(), mios_resolver::terminal_tuis::is_installed)
            })();
            return match result {
                Ok(Some(argv)) => {
                    use std::io::Write;
                    let mut out = std::io::stdout().lock();
                    for a in argv {
                        let _ = out.write_all(a.as_bytes());
                        let _ = out.write_all(b"\0");
                    }
                    ExitCode::SUCCESS
                }
                Ok(None) => ExitCode::from(tmux_os::HIDDEN),
                Err(error) => {
                    eprintln!("[tmux-menu] {error}");
                    ExitCode::FAILURE
                }
            };
        }
        Commands::TuiSpec { id, root, console } => {
            let root = resolve_root(root);
            let result = (|| -> Result<Option<Vec<String>>, String> {
                let console = match console.as_str() { "1" => true, "0" => false, _ => return Err("--console must be 0 or 1".into()) };
                let merged = mios_resolver::resolve_merged(Some(&root), false).map_err(|e| e.to_string())?;
                tmux_os::tui_spec(&merged, &id, console, mios_resolver::terminal_tuis::is_installed)
            })();
            return match result {
                Ok(Some(fields)) => {
                    use std::io::Write;
                    let mut out = std::io::stdout().lock();
                    for f in fields {
                        let _ = out.write_all(f.as_bytes());
                        let _ = out.write_all(b"\0");
                    }
                    ExitCode::SUCCESS
                }
                Ok(None) => ExitCode::from(tmux_os::HIDDEN),
                Err(error) => {
                    eprintln!("[tui-spec] {error}");
                    ExitCode::FAILURE
                }
            };
        }
        Commands::TmuxOs { root, args } => {
            let root = resolve_root(root);
            return match tmux_os::run(&root, &args) {
                Ok(code) => ExitCode::from(code),
                Err(error) => {
                    eprintln!("[tmux-os] {error}");
                    ExitCode::FAILURE
                }
            };
        }
        Commands::RenderHostTmux {
            root,
            font_verified,
        } => {
            let root = resolve_root(root);
            let result = mios_resolver::resolve_merged(Some(&root), false)
                .map_err(|e| e.to_string())
                .and_then(|merged| serde_json::to_value(merged).map_err(|e| e.to_string()))
                .and_then(|config| {
                    mios_service_core::launcher::host_tmux_config_with_font(&config, font_verified)
                });
            return match result {
                Ok(rendered) => {
                    print!("{rendered}");
                    ExitCode::SUCCESS
                }
                Err(error) => {
                    eprintln!("[host-tmux] {error}");
                    ExitCode::FAILURE
                }
            };
        }
        Commands::TerminalConfig {
            root,
            action,
            no_default,
        } => {
            let root = resolve_root(root);
            let result = mios_resolver::resolve_merged(Some(&root), false)
                .map_err(|e| (e.to_string(), 1))
                .and_then(|merged| serde_json::to_value(merged).map_err(|e| (e.to_string(), 1)))
                .and_then(|config| {
                    mios_service_core::launcher::terminal_config(&config, &action, !no_default)
                        .map_err(|e| (e, 1))
                })
                .map(|lines| println!("{}", lines.join("\n")));
            ("terminal-config", "runtime terminal policy", result)
        }
        Commands::CosignPolicy { root, check } => {
            let r = resolve_root(root);
            (
                "cosign-policy",
                "usr/lib/containers/policy.json",
                run_cosign_policy(&r, check),
            )
        }
        Commands::EgressFirewall { root, check } => {
            let r = resolve_root(root);
            (
                "egress-firewall",
                "usr/share/mios/security/egress.nft",
                run_egress_firewall(&r, check),
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
        Commands::RenderConsoleTheme { root, check, out } => {
            let r = resolve_root(root);
            (
                "render-console-theme",
                "usr/share/mios/theme/console-palette",
                match terminal::console::run(&r, check, out.as_deref()) {
                    Ok(msg) => {
                        if cli.format != "json" {
                            print!("{msg}");
                        }
                        Ok(())
                    }
                    Err(msg) => Err((msg, 1)),
                },
            )
        }
        Commands::TerminalColors { root, term } => {
            let r = resolve_root(root);
            let result = terminal::tmux_theme::tmux_runtime::load_layered(&r)
                .and_then(|data| terminal::console::escapes(&data, &term));
            (
                "terminal-colors",
                "stdout",
                match result {
                    Ok(msg) => {
                        if cli.format != "json" {
                            print!("{msg}");
                        }
                        Ok(())
                    }
                    Err(msg) => Err((msg, 1)),
                },
            )
        }
        Commands::BuildHyprlandPlugin { name, root, srpm } => {
            let r = resolve_root(root);
            (
                "build-hyprland-plugin",
                "usr/lib64/mios",
                match render::hyprglass_build::run_plugin(&r, srpm.as_deref(), name.as_str()) {
                    Ok(msg) => {
                        if cli.format != "json" {
                            print!("{msg}");
                        }
                        Ok(())
                    }
                    Err(msg) => Err((msg, 1)),
                },
            )
        }
        Commands::BuildHyprglass { root, srpm } => {
            let r = resolve_root(root);
            (
                "build-hyprglass",
                "usr/lib64/mios/hyprglass.so",
                match render::hyprglass_build::run(&r, srpm.as_deref()) {
                    Ok(msg) => {
                        if cli.format != "json" {
                            print!("{msg}");
                        }
                        Ok(())
                    }
                    Err(msg) => Err((msg, 1)),
                },
            )
        }
        Commands::NestedSession {root,dry_run,layout,output,seat,user,device,no_devices,plan,list_devices,list_sessions} => (
            "nested-session", "desktop",
            nested_session::run(&root.unwrap_or_else(||PathBuf::from("/")),dry_run,&nested_session::Options{target:layout,output,seat,user,devices:device,no_devices,plan,list_devices,list_sessions}).map_err(|e|(e,1)),
        ),
        Commands::KeymapOverlay {root,from_json} => (
            "keymap-overlay", "desktop",
            (|| -> Result<(), String> {
                let data: toml::Value = match from_json {
                    Some(p) => {
                        let mut raw = String::new();
                        if p.as_os_str() == "-" {
                            std::io::Read::read_to_string(&mut std::io::Read::take(std::io::stdin(), 16 << 20), &mut raw).map_err(|e| e.to_string())?;
                        } else {
                            raw = std::fs::read_to_string(&p).map_err(|e| format!("{}: {e}", p.display()))?;
                        }
                        serde_json::from_str(&raw).map_err(|e| format!("merged SSOT JSON: {e}"))?
                    }
                    None => mios_resolver::resolve_merged(Some(&root.unwrap_or_else(||PathBuf::from("/"))), false).map_err(|e| e.to_string())?,
                };
                println!("{}", global_bindings::keymap_overlay_json(&data)?);
                Ok(())
            })().map_err(|e|(e,1)),
        ),
        Commands::SessionScale {root,direction} => (
            "session-scale", "desktop",
            session_scale::Dir::parse(&direction)
                .and_then(|d| session_scale::run(&root.unwrap_or_else(||PathBuf::from("/")), d))
                .map(|msg| println!("{msg}"))
                .map_err(|e|(e,1)),
        ),
        Commands::Hyprland3dToggle => (
            "hyprland-3d-toggle", "desktop",
            render::render_hyprland::toggle_3d(Path::new("/")).map(|msg| { println!("{msg}"); }).map_err(|msg| (msg, 1)),
        ),
        Commands::HyprlandGreeter { kind, dry_run } => (
            "hyprland-greeter", "desktop",
            greeter::runtime(kind.as_deref(), dry_run).map(|msg| { println!("{msg}"); }).map_err(|msg| (msg, 1)),
        ),
        Commands::HyprlandBootProfile { hardware_profile, prepare_only, dry_run } => (
            "hyprland-boot-profile", "desktop",
            desktop_boot::select(&hardware_profile, prepare_only, dry_run).map(|msg| { println!("{msg}"); }).map_err(|msg| (msg, 1)),
        ),
        Commands::HyprlandSession { root, hardware_profile, dry_run } => {
            // Runtime launcher started by greetd from $HOME: never resolve the SSOT
            // relative to the working directory. Default to the live root.
            let r = root
                .or_else(|| env::var_os("MIOS_ROOT").filter(|v| !v.is_empty()).map(PathBuf::from))
                .unwrap_or_else(|| PathBuf::from("/"));
            (
                "hyprland-session",
                "desktop",
                match render::render_hyprland::session(&r, hardware_profile.as_deref(), dry_run) {
                    Ok(msg) => {
                        if cli.format != "json" {
                            print!("{msg}");
                        }
                        Ok(())
                    }
                    Err(msg) => Err((msg, 1)),
                },
            )
        }
        Commands::RenderHyprland {
            root,
            check,
            runtime,
            boot_entry,
        } => {
            let r = resolve_root(root);
            (
                "render-hyprland",
                "usr/share/mios/hyprland/hyprland.lua",
                match if let Some(entry) = boot_entry.as_deref() {
                    if runtime { Err(("boot-entry and runtime cannot be combined".into(), 1)) }
                    else { render::render_hyprland::run_boot_profiles(&r, entry, check) }
                } else { render::render_hyprland::run(&r, check, runtime) } {
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
        Commands::RenderTuned { root, check } => {
            let r = resolve_root(root);
            (
                "render-tuned",
                "usr/lib/tuned/profiles/*/tuned.conf",
                match render_tuned::run_render_tuned(&r, check) {
                    Ok(msg) => {
                        if cli.format != "json" {
                            println!("{msg}");
                        }
                        Ok(())
                    }
                    Err((msg, code)) => Err((msg, code)),
                },
            )
        }
        Commands::RenderAgentInteraction { root, home, runtime, check } => {
            let r = resolve_root(root);
            (
                "render-agent-interaction",
                "usr/share/mios/mios.toml [agent_cli.interaction]",
                match agent_interaction::run(&r, home.as_deref(), runtime, check) {
                    Ok(out) => {
                        // Skipped files are the operator's (unreadable or malformed): say so, never fail a launch.
                        for (name, why) in &out.skipped {
                            eprintln!("[agent-interaction] {name}: left untouched: {why}");
                        }
                        if check && !out.changed.is_empty() {
                            let list: Vec<String> = out.changed.iter().map(|p| p.display().to_string()).collect();
                            Err((format!("client files differ from [agent_cli.interaction]: {}", list.join(", ")), 1))
                        } else {
                            // Silent on a runtime projection: agent launches run it every time.
                            if cli.format != "json" && !runtime {
                                match &home {
                                    Some(h) => println!(
                                        "[agent-interaction] {} client(s) declared; {} updated, {} already current under {}",
                                        out.declared,
                                        out.changed.len(),
                                        out.unchanged.len(),
                                        h.display()
                                    ),
                                    None => println!("[agent-interaction] {} client(s) declared; table valid", out.declared),
                                }
                            }
                            Ok(())
                        }
                    }
                    Err(msg) => Err((msg, 1)),
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
            out_dir,
            list,
        } => {
            let r = resolve_root(root);
            (
                "render-manpages",
                "usr/share/man/**",
                match render_manpages::run_render_manpages(
                    &r,
                    check,
                    validate,
                    out_dir.as_deref(),
                    list,
                ) {
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
            runtime,
            style,
            status_position,
        } => {
            if let Some(dir) = runtime {
                let r = resolve_root(root);
                (
                    "render-tmux-theme",
                    "usr/share/mios/tmux/mios-theme.tmux.conf",
                    match tmux_theme::tmux_runtime::project_runtime(&r, &dir) {
                        // Silent on success: callers run this from login shells.
                        Ok(_) => Ok(()),
                        Err(msg) => Err((msg, 1)),
                    },
                )
            } else {
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
                                    println!(
                                        "[tmux-theme] tmux theme matches SSOT ({})",
                                        res.style
                                    );
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
        Commands::RenderGrub { root, sysroot, check, stage, apply, kernel_event, kernel_version } => {
            let r = root.unwrap_or_else(|| PathBuf::from("/"));
            let mode = if check {
                grub::Mode::Check
            } else if apply {
                grub::Mode::Apply
            } else if stage {
                grub::Mode::Stage
            } else {
                grub::Mode::Print
            };
            (
                "render-grub",
                "/etc/default/grub",
                match grub::run(&r, &sysroot, mode, kernel_event.as_deref(), kernel_version.as_deref()) {
                    Ok(msg) => {
                        if cli.format != "json" {
                            print!("{msg}");
                        }
                        Ok(())
                    }
                    Err(msg) => Err((msg, 1)),
                },
            )
        }
        Commands::RenderGreetd {
            root,
            sysroot,
            out,
            check,
        } => {
            let r = resolve_root(root);
            let stdout = out.is_none() && check.is_none();
            let mode = match (out, check) {
                (Some(p), _) => greeter::Mode::Write(p),
                (None, Some(p)) => greeter::Mode::Check(p),
                (None, None) => greeter::Mode::Stdout,
            };
            (
                "render-greetd",
                "/etc/greetd/config.toml",
                match greeter::run(&r, &sysroot, mode) {
                    Ok(res) => {
                        for why in &res.degraded {
                            eprintln!("[greetd] WARNING: glass greeter degraded to tuigreet: {why}");
                        }
                        if cli.format != "json" && !stdout {
                            println!("[greetd] greeter = {} (projection {} bytes)", res.effective, res.text.len());
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
                fastfetch::FASTFETCH_FIXTURE,
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
                                println!(
                                    "[fastfetch] SUCCESS: Generated Fastfetch config ({} lines, {} bytes)",
                                    res.lines_count, res.jsonc_len
                                );
                                if let Some(target) = &res.target {
                                    println!("  Saved config: {}", target.display());
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
            || msg.starts_with("[fastfetch]")
            || msg.starts_with("fastfetch")
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

// `dashboard`: renders resolved host facts through the shared engine to stdout; writes nothing.
mod dashboard {
    use serde_json::Value;
    use std::io::Read;
    use std::path::Path;
    use std::process::Command;
    use std::time::Duration;

    fn facts(timeout: Duration) -> Result<Value, String> {
        let mut command = Command::new("fastfetch");
        command.args([
            "--config",
            "none",
            "--format",
            "json",
            "--structure",
            "Title:OS:Kernel:Uptime:CPU:GPU:Memory:Swap:Disk:Shell:Host:TerminalFont:DateTime",
        ]);
        let output = mios_service_core::process::output_timeout(&mut command, timeout)?;
        if !output.status.success() {
            return Err(format!(
                "fastfetch exited unsuccessfully: {}",
                String::from_utf8_lossy(&output.stderr)
            ));
        }
        serde_json::from_slice(&output.stdout)
            .map_err(|e| format!("Invalid structured fastfetch facts: {e}"))
    }

    pub fn run(
        root: &Path,
        resolved_stdin: bool,
        width: Option<usize>,
        no_probe: bool,
        json: bool,
        facts_file: Option<&Path>,
    ) -> Result<String, String> {
        let doc: Value = if resolved_stdin {
            let mut bytes = Vec::new();
            std::io::stdin()
                .take(8 * 1024 * 1024 + 1)
                .read_to_end(&mut bytes)
                .map_err(|e| format!("Resolver input: {e}"))?;
            if bytes.len() > 8 * 1024 * 1024 {
                return Err("Resolver input exceeds 8 MiB".into());
            }
            let input: Value = serde_json::from_slice(&bytes)
                .map_err(|e| format!("Invalid resolver JSON: {e}"))?;
            input
                .get("merged")
                .cloned()
                .ok_or("Native resolver JSON omitted merged SSOT")?
        } else {
            serde_json::to_value(
                mios_resolver::resolve_merged(Some(root), false).map_err(|e| e.to_string())?,
            )
            .map_err(|e| e.to_string())?
        };
        let width = width
            .or_else(|| std::env::var("COLUMNS").ok().and_then(|v| v.parse().ok()))
            .or_else(|| {
                doc["terminal"]["cols"]
                    .as_u64()
                    .and_then(|v| usize::try_from(v).ok())
            })
            .ok_or("Missing SSOT terminal.cols")?;
        let timeout = mios_service_core::dashboard::milliseconds(&doc, "facts_timeout_ms")?;
        let mut endpoints = mios_service_core::dashboard::catalog(&doc)?;
        if !no_probe {
            mios_service_core::dashboard::probe(
                &mut endpoints,
                mios_service_core::dashboard::milliseconds(&doc, "probe_timeout_ms")?,
            )?;
        }
        let facts = if let Some(path) = facts_file {
            serde_json::from_slice(&std::fs::read(path).map_err(|e| e.to_string())?)
                .map_err(|e| format!("Invalid facts input: {e}"))?
        } else {
            match facts(timeout) {
                Ok(facts) => facts,
                Err(error) => {
                    eprintln!("[dashboard] {error}; hardware facts unavailable");
                    Value::Array(Vec::new())
                }
            }
        };
        if json {
            // Validate render inputs even for the machine-readable parity surface.
            mios_service_core::dashboard::render(&doc, &facts, &endpoints, width)?;
            serde_json::to_string_pretty(&serde_json::json!({"schema":"mios.dashboard.v1", "metrics":mios_service_core::dashboard::metrics(&doc, &facts)?, "endpoints":endpoints})).map_err(|e| e.to_string())
        } else {
            mios_service_core::dashboard::render(&doc, &facts, &endpoints, width)
        }
    }
}

// `projection-evidence`: drift diagnostics, rendered into a private directory; writes nothing in the tree.
mod projection_evidence {
    use std::collections::BTreeSet;
    use std::fs;
    use std::path::{Component, Path};
    use std::process::{Command, Output};

    const PREFIX: &str = "[98-drift-checks][diff]";

    fn relative(name: &str) -> Result<&Path, String> {
        let path = Path::new(name);
        if name.is_empty()
            || path
                .components()
                .any(|c| !matches!(c, Component::Normal(_)))
        {
            return Err(format!("projection path must stay beneath root: {name}"));
        }
        Ok(path)
    }

    fn successful(output: Output, action: &str) -> Result<Vec<u8>, String> {
        if !output.status.success() {
            return Err(format!(
                "{action}: {}: {}",
                output.status,
                String::from_utf8_lossy(&output.stderr)
                    .chars()
                    .take(4096)
                    .collect::<String>()
            ));
        }
        Ok(output.stdout)
    }

    pub fn run(root: &Path, generator: &str, targets: &[String]) -> Result<(), String> {
        if !matches!(generator, "cargo-manifests" | "gate-index" | "bib-configs")
            || targets.is_empty()
        {
            return Err("native projection and nonempty target list required".into());
        }
        // Keep ordinary absolute paths for Git: Windows canonicalization adds a
        // verbatim prefix that Git's no-index hashing does not consistently accept.
        let actual_root = root.canonicalize().map_err(|e| e.to_string())?;
        let root = std::path::absolute(root).map_err(|e| e.to_string())?;
        let census = successful(
            Command::new("git")
                .arg("-C")
                .arg(&root)
                .args(["ls-files", "-z"])
                .output()
                .map_err(|e| e.to_string())?,
            "tracked census",
        )?;
        let census = String::from_utf8(census).map_err(|e| e.to_string())?;
        let tracked: BTreeSet<_> = census.split('\0').filter(|s| !s.is_empty()).collect();
        if tracked.is_empty() {
            return Err("tracked census is empty; no projection evidence collected".into());
        }
        for name in targets {
            relative(name)?;
            if !tracked.contains(name.as_str()) {
                return Err(format!("projection target is not tracked: {name}"));
            }
        }
        let snapshot = tempfile::tempdir().map_err(|e| e.to_string())?;
        for name in &tracked {
            let path = relative(name)?;
            let source = root.join(path);
            let resolved = source.canonicalize().map_err(|e| format!("{name}: {e}"))?;
            if !resolved.starts_with(&actual_root) || !resolved.is_file() {
                return Err(format!(
                    "tracked input escapes root or is not a file: {name}"
                ));
            }
            let destination = snapshot.path().join(path);
            fs::create_dir_all(destination.parent().ok_or("missing snapshot parent")?)
                .map_err(|e| e.to_string())?;
            fs::copy(&resolved, destination).map_err(|e| format!("copy {name}: {e}"))?;
        }
        let mut command = Command::new(std::env::current_exe().map_err(|e| e.to_string())?);
        command
            .arg(generator)
            .arg("--root")
            .arg(snapshot.path())
            .current_dir(snapshot.path());
        for (key, _) in std::env::vars_os() {
            if key.to_string_lossy().starts_with("MIOS_") {
                command.env_remove(key);
            }
        }
        command
            .env("MIOS_DRIFT_ROOT", snapshot.path())
            .env("MIOS_ROOT", snapshot.path());
        successful(
            command.output().map_err(|e| e.to_string())?,
            "native rendering",
        )?;
        eprintln!(
            "{PREFIX} generator: mios-gen {generator} --root <private tracked-byte snapshot>"
        );
        for name in targets {
            let expected = snapshot.path().join(relative(name)?);
            if !expected.is_file() {
                return Err(format!("native rendering produced no target: {name}"));
            }
            let diff = Command::new("git")
                .current_dir(snapshot.path())
                .args([
                    "--no-pager",
                    "diff",
                    "--no-index",
                    "--no-ext-diff",
                    "--no-textconv",
                    "--",
                ])
                .arg(root.join(name))
                .arg(expected)
                .output()
                .map_err(|e| e.to_string())?;
            if !matches!(diff.status.code(), Some(0 | 1)) {
                return Err(format!(
                    "diff {name}: {}: {}",
                    diff.status,
                    String::from_utf8_lossy(&diff.stderr)
                ));
            }
            eprintln!("{PREFIX} target {name}: ACTUAL on-disk -> GENERATED from SSOT");
            let text = String::from_utf8_lossy(&diff.stdout);
            for line in text.lines().take(200) {
                eprintln!("{PREFIX} {line}");
            }
            if text.lines().count() > 200 {
                eprintln!("{PREFIX} remaining diff lines omitted");
            }
        }
        Ok(())
    }
}

// `archive`: the ADR-0030 Decision 7 legacy store, a bare sha256 repo beside .git, from [legibility.archive].
// Sections: config, git, replay, dropped set, layout, compare, build.
mod archive {
    use sha2::{Digest, Sha256};
    use std::collections::{BTreeMap, BTreeSet};
    use std::fs;
    use std::io::Write;
    use std::path::{Component, Path, PathBuf};
    use std::process::{Command, Stdio};

    /// The gate contract shared with mios-gate and mios-probe: 1 violations, 2 could not run.
    pub const EXIT_VIOLATIONS: u8 = 1;
    pub const EXIT_CANNOT_RUN: u8 = 2;

    const SHALLOW: &str = "full history required (fetch-depth: 0)";

    pub enum Failure {
        Violations(Vec<String>),
        CannotRun(String),
    }

    fn violation(finding: impl Into<String>) -> Failure {
        Failure::Violations(vec![finding.into()])
    }

    // ---- config: [legibility.archive] ----------------------------------------------------

    struct Config {
        path: String,
        refname: String,
        object_format: String,
        since: String,
        exclude: Vec<String>,
        author: String,
    }

    fn config(root: &Path) -> Result<Config, Failure> {
        let ssot = mios_resolver::resolve_projection(root).map_err(|e| {
            Failure::CannotRun(format!(
                "the SSOT under {} did not resolve: {e}",
                root.display()
            ))
        })?;
        let table = ssot
            .get("legibility")
            .and_then(|l| l.get("archive"))
            .and_then(|a| a.as_table())
            .ok_or_else(|| {
                Failure::CannotRun("mios.toml declares no [legibility.archive] table".into())
            })?;
        let text = |key: &str| -> Result<String, Failure> {
            table
                .get(key)
                .and_then(|v| v.as_str())
                .map(str::trim)
                .filter(|v| !v.is_empty())
                .map(str::to_string)
                .ok_or_else(|| {
                    Failure::CannotRun(format!(
                        "[legibility.archive].{key} is absent or not a non-empty string"
                    ))
                })
        };
        let exclude = match table.get("exclude") {
            Some(toml::Value::Array(items)) => items
                .iter()
                .map(|v| v.as_str().filter(|s| !s.is_empty()).map(str::to_string))
                .collect::<Option<Vec<_>>>()
                .ok_or_else(|| {
                    Failure::CannotRun(
                        "[legibility.archive].exclude must list non-empty glob strings".into(),
                    )
                })?,
            _ => {
                return Err(Failure::CannotRun(
                    "[legibility.archive].exclude is absent (an empty list is explicit)".into(),
                ))
            }
        };
        let cfg = Config {
            path: text("path")?,
            refname: text("ref")?,
            object_format: text("object_format")?,
            since: text("since")?,
            exclude,
            author: text("author")?,
        };
        let path = Path::new(&cfg.path);
        if cfg.path == ".git"
            || cfg.path.contains('\\')
            || path.components().count() != 1
            || !matches!(path.components().next(), Some(Component::Normal(_)))
        {
            return Err(Failure::CannotRun(format!(
                "[legibility.archive].path must be one directory name at the root, not {:?}",
                cfg.path
            )));
        }
        if cfg.object_format != "sha256" {
            // The comparison hashes bytes with sha256; another format needs its own digest.
            return Err(Failure::CannotRun(format!(
                "[legibility.archive].object_format {:?} is not supported; the store is sha256",
                cfg.object_format
            )));
        }
        let (name, email) = cfg.author.rsplit_once(" <").unwrap_or(("", ""));
        let email_ok = email.len() > 1
            && email.ends_with('>')
            && !email[..email.len() - 1].contains(['<', '>']);
        if name.trim().is_empty() || !email_ok || cfg.author.contains('\n') {
            return Err(Failure::CannotRun(format!(
                "[legibility.archive].author must read \"Name <email>\", not {:?}",
                cfg.author
            )));
        }
        Ok(cfg)
    }

    /// fnmatchcase for `*` (which also crosses `/`, as in [legibility.file_budget]) and `?`.
    fn glob(pattern: &[u8], text: &[u8]) -> bool {
        let (mut p, mut t, mut star, mut mark) = (0, 0, None, 0);
        while t < text.len() {
            if p < pattern.len() && pattern[p] == b'*' {
                star = Some(p);
                mark = t;
                p += 1;
            } else if p < pattern.len() && (pattern[p] == b'?' || pattern[p] == text[t]) {
                p += 1;
                t += 1;
            } else if let Some(s) = star {
                p = s + 1;
                mark += 1;
                t = mark;
            } else {
                return false;
            }
        }
        pattern[p..].iter().all(|b| *b == b'*')
    }

    // ---- git plumbing ---------------------------------------------------------------------

    /// Variables that would point git at another repository, index or object store.
    const REDIRECTS: [&str; 7] = [
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_COMMON_DIR",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_NAMESPACE",
    ];

    /// One git invocation against the checkout (`git_dir` None) or a bare store.
    fn git(
        root: &Path,
        git_dir: Option<&Path>,
        args: &[&str],
        input: Option<&[u8]>,
    ) -> Result<Vec<u8>, String> {
        let mut command = Command::new("git");
        for name in REDIRECTS {
            command.env_remove(name);
        }
        match git_dir {
            Some(dir) => command.arg("--git-dir").arg(dir),
            None => command.arg("-C").arg(root),
        };
        command
            .args(["-c", "core.quotepath=false", "-c", "color.ui=false"])
            .args(args)
            .stdin(if input.is_some() {
                Stdio::piped()
            } else {
                Stdio::null()
            })
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        let what = || format!("git {}", args.join(" "));
        let mut child = command.spawn().map_err(|e| format!("{}: {e}", what()))?;
        let feeder = match (input, child.stdin.take()) {
            (Some(bytes), Some(mut stdin)) => {
                let bytes = bytes.to_vec();
                Some(std::thread::spawn(move || stdin.write_all(&bytes)))
            }
            _ => None,
        };
        let output = child
            .wait_with_output()
            .map_err(|e| format!("{}: {e}", what()))?;
        if let Some(feeder) = feeder {
            feeder
                .join()
                .map_err(|_| format!("{}: the input writer panicked", what()))?
                .map_err(|e| format!("{}: writing input: {e}", what()))?;
        }
        if !output.status.success() {
            return Err(format!(
                "{} exited {}: {}",
                what(),
                output.status,
                String::from_utf8_lossy(&output.stderr).trim()
            ));
        }
        Ok(output.stdout)
    }

    fn line(bytes: Vec<u8>) -> String {
        String::from_utf8_lossy(&bytes).trim().to_string()
    }

    fn records(raw: &[u8]) -> impl Iterator<Item = &[u8]> {
        raw.split(|b| *b == 0).filter(|r| !r.is_empty())
    }

    fn utf8(bytes: &[u8]) -> Result<String, String> {
        String::from_utf8(bytes.to_vec()).map_err(|_| {
            format!(
                "a git path is not UTF-8: {}",
                String::from_utf8_lossy(bytes)
            )
        })
    }

    fn is_oid(s: &str) -> bool {
        matches!(s.len(), 40 | 64) && s.bytes().all(|b| b.is_ascii_hexdigit())
    }

    #[derive(Clone, PartialEq, Eq, Debug)]
    struct Blob {
        mode: String,
        oid: String,
    }

    /// `git ls-tree -r -z --full-tree`: path -> mode and object id.
    fn parse_tree(raw: &[u8]) -> Result<BTreeMap<String, Blob>, String> {
        let mut tree = BTreeMap::new();
        for rec in records(raw) {
            let tab = rec
                .iter()
                .position(|b| *b == b'\t')
                .ok_or("ls-tree record without a tab")?;
            let meta = utf8(&rec[..tab])?;
            let fields: Vec<&str> = meta.split(' ').collect();
            if fields.len() != 3 || !is_oid(fields[2]) {
                return Err(format!("unparsable ls-tree record: {meta}"));
            }
            let blob = Blob {
                mode: fields[0].to_string(),
                oid: fields[2].to_string(),
            };
            tree.insert(utf8(&rec[tab + 1..])?, blob);
        }
        Ok(tree)
    }

    /// `cat-file --batch` over every object id: id -> bytes. A short answer is an error.
    fn blobs(
        root: &Path,
        git_dir: Option<&Path>,
        oids: &BTreeSet<String>,
    ) -> Result<BTreeMap<String, Vec<u8>>, String> {
        let mut out = BTreeMap::new();
        if oids.is_empty() {
            return Ok(out);
        }
        let input: Vec<u8> = oids
            .iter()
            .flat_map(|o| format!("{o}\n").into_bytes())
            .collect();
        let raw = git(root, git_dir, &["cat-file", "--batch"], Some(&input))?;
        let mut at = 0;
        while at < raw.len() {
            let eol = raw[at..]
                .iter()
                .position(|b| *b == b'\n')
                .ok_or("truncated cat-file header")?
                + at;
            let header = String::from_utf8_lossy(&raw[at..eol]).to_string();
            let fields: Vec<&str> = header.split(' ').collect();
            if fields.len() != 3 || fields[1] != "blob" {
                return Err(format!("cat-file did not return a blob: {header}"));
            }
            let size: usize = fields[2]
                .parse()
                .map_err(|_| format!("bad cat-file size: {header}"))?;
            let start = eol + 1;
            if start + size > raw.len() {
                return Err(format!("truncated cat-file body for {}", fields[0]));
            }
            out.insert(fields[0].to_string(), raw[start..start + size].to_vec());
            at = start + size + 1;
        }
        if out.len() != oids.len() {
            return Err(format!(
                "cat-file returned {} of {} objects",
                out.len(),
                oids.len()
            ));
        }
        Ok(out)
    }

    // ---- history replay --------------------------------------------------------------------

    /// A path's newest content and mode, and the newest first-parent commit that held it.
    #[derive(Clone, Debug)]
    struct Last {
        blob: Blob,
        commit: String,
    }

    struct History {
        since: String,
        head: String,
        last: BTreeMap<String, Last>,
    }

    /// The first-parent commits of since..HEAD, oldest first, and the base the oldest one was
    /// made on (None: a root commit), once every link of the walk is proven present.
    fn chain(
        root: &Path,
        since: &str,
        head: &str,
    ) -> Result<(Vec<String>, Option<String>), Failure> {
        let shallow = git(root, None, &["rev-parse", "--is-shallow-repository"], None)
            .map(line)
            .unwrap_or_default()
            == "true";
        let hint = if shallow {
            " (this clone is shallow)"
        } else {
            ""
        };
        let ancestry = ["merge-base", "--is-ancestor", since, head];
        if git(root, None, &ancestry, None).is_err() {
            return Err(violation(format!(
                "{SHALLOW}: {since} is not an ancestor of HEAD in this clone{hint}"
            )));
        }
        let range = format!("{since}..{head}");
        let walk = ["rev-list", "--first-parent", "--parents", &range];
        let raw = git(root, None, &walk, None).map_err(|e| violation(format!("{SHALLOW}: {e}")))?;
        let rows: Vec<Vec<String>> = String::from_utf8_lossy(&raw)
            .lines()
            .map(|l| l.split(' ').map(str::to_string).collect())
            .collect();
        for (i, row) in rows.iter().enumerate() {
            match (row.get(1), rows.get(i + 1)) {
                (Some(parent), Some(next)) if *parent != next[0] => {
                    return Err(violation(format!(
                        "history walk: {} has first parent {parent}, but the walk continued at {}",
                        row[0], next[0]
                    )))
                }
                (None, _) if shallow => {
                    return Err(violation(format!(
                        "{SHALLOW}: history stops at {} before reaching {since}{hint}",
                        row[0]
                    )))
                }
                (None, Some(_)) => {
                    return Err(violation(format!(
                        "history walk: root commit {} is not the oldest of since..HEAD",
                        row[0]
                    )))
                }
                _ => {}
            }
        }
        // An empty walk from an ancestor means since IS HEAD: the replay starts at HEAD.
        let base = match rows.last() {
            Some(oldest) => oldest.get(1).cloned(),
            None => Some(head.to_string()),
        };
        let commits = rows.into_iter().rev().map(|r| r[0].clone()).collect();
        Ok((commits, base))
    }

    fn tree_at(root: &Path, commit: &str) -> Result<BTreeMap<String, Blob>, Failure> {
        git(
            root,
            None,
            &["ls-tree", "-r", "-z", "--full-tree", commit],
            None,
        )
        .and_then(|raw| parse_tree(&raw))
        .map_err(Failure::CannotRun)
    }

    /// Replay every first-parent diff of since..HEAD from its base, prove the replay reproduces
    /// HEAD's tree, and keep each path's newest version: a commit of the walk, else the since tree.
    fn replay(root: &Path, cfg: &Config) -> Result<History, Failure> {
        let inside = git(root, None, &["rev-parse", "--is-inside-work-tree"], None).map(line);
        if inside.as_deref() != Ok("true") {
            return Err(Failure::CannotRun(format!(
                "{} is not inside a git checkout",
                root.display()
            )));
        }
        let cdup =
            git(root, None, &["rev-parse", "--show-cdup"], None).map_err(Failure::CannotRun)?;
        if !line(cdup).is_empty() {
            return Err(Failure::CannotRun(format!(
                "{} is not the root of its checkout",
                root.display()
            )));
        }
        let head = git(
            root,
            None,
            &["rev-parse", "--verify", "--quiet", "HEAD^{commit}"],
            None,
        )
        .map(line)
        .map_err(|_| Failure::CannotRun("HEAD names no commit".into()))?;
        let since_rev = format!("{}^{{commit}}", cfg.since);
        let since = git(
            root,
            None,
            &["rev-parse", "--verify", "--quiet", &since_rev],
            None,
        )
        .map(line)
        .map_err(|_| {
            violation(format!(
                "{SHALLOW}: [legibility.archive].since {} is not in this clone",
                cfg.since
            ))
        })?;
        let (commits, base) = chain(root, &since, &head)?;

        let mut cur = match &base {
            Some(base) => tree_at(root, base)?,
            None => BTreeMap::new(),
        };
        let mut last: BTreeMap<String, Last> = BTreeMap::new();
        let range = format!("{since}..{head}");
        let raw = if commits.is_empty() {
            Vec::new()
        } else {
            let args = [
                "log",
                "--first-parent",
                "--diff-merges=first-parent",
                "--raw",
                "-z",
                "--no-renames",
                "--no-abbrev",
                "--reverse",
                "--no-color",
                "--no-notes",
                "--no-show-signature",
                "--format=%H",
                &range,
            ];
            git(root, None, &args, None).map_err(Failure::CannotRun)?
        };
        // `prev` is the walk's commit before `commit`; None while `commit` is the oldest, whose
        // parent (the base) is outside since..HEAD, so a path only the base held is not counted.
        let (mut seen, mut prev, mut commit) = (Vec::new(), None::<String>, String::new());
        let mut tokens = raw
            .split(|b| *b == 0)
            .map(|t| t.strip_prefix(b"\n").unwrap_or(t));
        while let Some(token) = tokens.next() {
            if token.is_empty() {
                continue;
            }
            if let Some(meta) = token.strip_prefix(b":") {
                let meta = String::from_utf8_lossy(meta).to_string();
                let path = tokens
                    .next()
                    .ok_or_else(|| violation(format!("history replay: :{meta} has no path")))
                    .and_then(|p| utf8(p).map_err(Failure::CannotRun))?;
                let f: Vec<&str> = meta.split(' ').collect();
                if f.len() != 5 || commit.is_empty() {
                    return Err(violation(format!(
                        "history replay: unparsable diff record :{meta}"
                    )));
                }
                let old = Blob {
                    mode: f[0].into(),
                    oid: f[2].into(),
                };
                let new = Blob {
                    mode: f[1].into(),
                    oid: f[3].into(),
                };
                let added = f[4].starts_with('A');
                if (added && cur.contains_key(&path)) || (!added && cur.get(&path) != Some(&old)) {
                    return Err(violation(format!(
                        "history replay diverged at {commit} on {path}: the diff does not apply to its first parent"
                    )));
                }
                if f[4].starts_with('D') {
                    cur.remove(&path);
                    if let Some(parent) = &prev {
                        let commit = parent.clone();
                        last.insert(path, Last { blob: old, commit });
                    }
                } else {
                    cur.insert(path, new);
                }
                continue;
            }
            let id = String::from_utf8_lossy(token).to_string();
            if !is_oid(&id) {
                return Err(violation(format!(
                    "history replay: unexpected log token {id:?}"
                )));
            }
            if !commit.is_empty() {
                prev = Some(std::mem::take(&mut commit));
            }
            commit.clone_from(&id);
            seen.push(id);
        }
        if seen != commits {
            return Err(violation(format!(
                "history replay saw {} commits where the first-parent walk has {}",
                seen.len(),
                commits.len()
            )));
        }
        let at_head = tree_at(root, &head)?;
        if cur != at_head {
            let first = cur
                .keys()
                .chain(at_head.keys())
                .find(|p| cur.get(*p) != at_head.get(*p))
                .cloned()
                .unwrap_or_default();
            return Err(violation(format!(
                "history replay does not reproduce HEAD's tree (first difference: {first})"
            )));
        }
        for (path, blob) in cur {
            let commit = head.clone();
            last.insert(path, Last { blob, commit });
        }
        for (path, blob) in tree_at(root, &since)? {
            let commit = since.clone();
            last.entry(path).or_insert(Last { blob, commit });
        }
        Ok(History { since, head, last })
    }

    // ---- dropped set --------------------------------------------------------------------------

    struct Dropped {
        files: BTreeMap<String, Last>,
        excluded: usize,
        unused_excludes: Vec<String>,
    }

    fn under(path: &str, dir: &str) -> bool {
        path == dir || path.strip_prefix(dir).is_some_and(|r| r.starts_with('/'))
    }

    /// Every path history held that the index no longer tracks, less the store and exclusions.
    fn dropped(root: &Path, cfg: &Config, history: &History) -> Result<Dropped, Failure> {
        let staged =
            git(root, None, &["ls-files", "-s", "-z"], None).map_err(Failure::CannotRun)?;
        let mut tracked = BTreeSet::new();
        for rec in records(&staged) {
            let tab = rec
                .iter()
                .position(|b| *b == b'\t')
                .ok_or_else(|| Failure::CannotRun("ls-files record without a tab".into()))?;
            tracked.insert(utf8(&rec[tab + 1..]).map_err(Failure::CannotRun)?);
        }
        let mut files = BTreeMap::new();
        let mut used = BTreeSet::new();
        let mut excluded = 0;
        for (path, last) in &history.last {
            if tracked.contains(path) || under(path, &cfg.path) {
                continue;
            }
            let hits: Vec<&String> = cfg
                .exclude
                .iter()
                .filter(|g| glob(g.as_bytes(), path.as_bytes()))
                .collect();
            if hits.is_empty() {
                files.insert(path.clone(), last.clone());
            } else {
                excluded += 1;
                used.extend(hits);
            }
        }
        let mut problems: Vec<String> = files
            .iter()
            .filter(|(_, l)| !matches!(l.blob.mode.as_str(), "100644" | "100755" | "120000"))
            .map(|(p, l)| format!("{p}: mode {} cannot be archived as a file", l.blob.mode))
            .collect();
        let clash = files.keys().find(|p| {
            files
                .range(format!("{p}/")..)
                .next()
                .is_some_and(|(q, _)| under(q, p))
        });
        if let Some(clash) = clash {
            problems.push(format!(
                "{clash} was both a file and a directory in the dropped set; one tree cannot hold both"
            ));
        }
        if !problems.is_empty() {
            return Err(Failure::Violations(problems));
        }
        let unused_excludes = cfg
            .exclude
            .iter()
            .filter(|g| !used.contains(g))
            .cloned()
            .collect();
        Ok(Dropped {
            files,
            excluded,
            unused_excludes,
        })
    }

    fn digest(bytes: &[u8]) -> String {
        Sha256::digest(bytes)
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect()
    }

    // ---- layout ---------------------------------------------------------------------------------

    fn head_text(cfg: &Config) -> String {
        format!("ref: refs/heads/{}\n", cfg.refname)
    }

    fn config_text(cfg: &Config) -> String {
        format!(
            "[core]\n\trepositoryformatversion = 1\n\tbare = true\n[extensions]\n\tobjectformat = {}\n",
            cfg.object_format
        )
    }

    /// Every non-directory under the store, slash-separated and relative to it.
    fn listing(store: &Path) -> Result<BTreeSet<String>, String> {
        let mut files = BTreeSet::new();
        for entry in walkdir::WalkDir::new(store).min_depth(1) {
            let entry = entry.map_err(|e| e.to_string())?;
            if entry.file_type().is_dir() {
                continue;
            }
            let rel = entry
                .path()
                .strip_prefix(store)
                .map_err(|e| e.to_string())?;
            let parts: Vec<String> = rel
                .components()
                .map(|c| c.as_os_str().to_string_lossy().to_string())
                .collect();
            files.insert(parts.join("/"));
        }
        Ok(files)
    }

    /// HEAD, config, the one branch and one pack (.pack and .idx, .rev optional); nothing else.
    fn layout(root: &Path, cfg: &Config, store: &Path) -> Vec<String> {
        let files = match listing(store) {
            Ok(files) => files,
            Err(e) => return vec![format!("{} could not be listed: {e}", cfg.path)],
        };
        let mut bad = Vec::new();
        let branch = format!("refs/heads/{}", cfg.refname);
        let mut packs = BTreeMap::<String, BTreeSet<String>>::new();
        for file in &files {
            let pack = file
                .strip_prefix("objects/pack/pack-")
                .and_then(|n| n.rsplit_once('.'))
                .filter(|(hash, ext)| is_oid(hash) && matches!(*ext, "pack" | "idx" | "rev"));
            match pack {
                Some((hash, ext)) => {
                    packs
                        .entry(hash.to_string())
                        .or_default()
                        .insert(ext.to_string());
                }
                None if file == "HEAD" || file == "config" || *file == branch => {}
                None => bad.push(format!(
                    "{}/{file} is outside the allowed set (HEAD, config, {branch}, one pack); delete it or rebuild: mios-gen archive",
                    cfg.path
                )),
            }
        }
        let whole = packs
            .values()
            .all(|exts| exts.contains("pack") && exts.contains("idx"));
        if packs.len() != 1 || !whole {
            bad.push(format!(
                "{} must hold exactly one pack with its .idx (found {}); rebuild: mios-gen archive",
                cfg.path,
                packs.len()
            ));
        }
        for (name, want) in [("HEAD", head_text(cfg)), ("config", config_text(cfg))] {
            if fs::read(store.join(name)).ok().as_deref() != Some(want.as_bytes()) {
                bad.push(format!(
                    "{}/{name} is not the minimal form mios-gen archive writes ({want:?})",
                    cfg.path
                ));
            }
        }
        let spec = format!(":(literal){}", cfg.path);
        let prefix = format!("{}/", cfg.path);
        match git(root, None, &["ls-files", "-z", "--", &spec], None) {
            Ok(raw) => {
                let tracked: BTreeSet<String> = records(&raw)
                    .filter_map(|r| utf8(r).ok())
                    .filter_map(|p| p.strip_prefix(&prefix).map(str::to_string))
                    .collect();
                for file in files.difference(&tracked) {
                    bad.push(format!(
                        "{}/{file} is not tracked; stage it with the rest of the archive",
                        cfg.path
                    ));
                }
                for file in tracked.difference(&files) {
                    bad.push(format!(
                        "{}/{file} is tracked but absent from the worktree",
                        cfg.path
                    ));
                }
            }
            Err(e) => bad.push(format!("tracked archive files could not be listed: {e}")),
        }
        bad
    }

    // ---- compare --------------------------------------------------------------------------------

    struct Expected {
        cfg: Config,
        history: History,
        dropped: Dropped,
        bytes: BTreeMap<String, Vec<u8>>,
    }

    fn expected(root: &Path) -> Result<Expected, Failure> {
        let cfg = config(root)?;
        let history = replay(root, &cfg)?;
        let dropped = dropped(root, &cfg, &history)?;
        let oids: BTreeSet<String> = dropped.files.values().map(|l| l.blob.oid.clone()).collect();
        let bytes = blobs(root, None, &oids).map_err(Failure::CannotRun)?;
        Ok(Expected {
            cfg,
            history,
            dropped,
            bytes,
        })
    }

    fn total(want: &Expected) -> usize {
        want.dropped
            .files
            .values()
            .map(|l| want.bytes[&l.blob.oid].len())
            .sum()
    }

    /// Every difference between the store and the recomputed dropped set; empty is current.
    fn compare(root: &Path, want: &Expected) -> Vec<String> {
        let cfg = &want.cfg;
        let store = root.join(&cfg.path);
        if !store.is_dir() {
            return vec![format!(
                "{} does not exist; build it: mios-gen archive",
                cfg.path
            )];
        }
        let mut bad = layout(root, cfg, &store);
        for glob in &want.dropped.unused_excludes {
            bad.push(format!(
                "[legibility.archive].exclude entry {glob:?} matches no dropped path; remove it"
            ));
        }
        let fsck = ["fsck", "--strict", "--no-dangling", "--no-progress"];
        if let Err(e) = git(root, Some(&store), &fsck, None) {
            bad.push(format!("{} fails git fsck --strict: {e}", cfg.path));
            return bad;
        }
        let branch = format!("refs/heads/{}", cfg.refname);
        let listed = git(
            root,
            Some(&store),
            &["ls-tree", "-r", "-z", "--full-tree", &branch],
            None,
        )
        .and_then(|raw| parse_tree(&raw));
        let tree = match listed {
            Ok(tree) => tree,
            Err(e) => {
                bad.push(format!("{} has no readable {branch}: {e}", cfg.path));
                return bad;
            }
        };
        let oids = tree.values().map(|b| b.oid.clone()).collect();
        let have = match blobs(root, Some(&store), &oids) {
            Ok(have) => have,
            Err(e) => {
                bad.push(format!("{} blobs could not be read: {e}", cfg.path));
                return bad;
            }
        };
        for (path, last) in &want.dropped.files {
            let Some(blob) = tree.get(path) else {
                bad.push(format!(
                    "missing: {path} (dropped; last held by {})",
                    last.commit
                ));
                continue;
            };
            if blob.mode != last.blob.mode {
                bad.push(format!(
                    "mode differs: {path} (archive {}, last {})",
                    blob.mode, last.blob.mode
                ));
            }
            let (archived, original) = (
                digest(&have[&blob.oid]),
                digest(&want.bytes[&last.blob.oid]),
            );
            if archived != original {
                bad.push(format!(
                    "content differs: {path} (archive sha256 {archived}, last sha256 {original} in {})",
                    last.commit
                ));
            }
        }
        for path in tree.keys().filter(|p| !want.dropped.files.contains_key(*p)) {
            bad.push(format!(
                "extra: {path} (archived, but not in the dropped set)"
            ));
        }
        bad
    }

    // ---- build ----------------------------------------------------------------------------------

    /// C-style quoting, which git fast-import accepts for every path.
    fn quote(path: &str) -> String {
        let mut q = String::from("\"");
        for b in path.bytes() {
            match b {
                b'"' => q.push_str("\\\""),
                b'\\' => q.push_str("\\\\"),
                0x20..=0x7e => q.push(b as char),
                _ => q.push_str(&format!("\\{b:03o}")),
            }
        }
        q.push('"');
        q
    }

    fn message(want: &Expected) -> String {
        let h = &want.history;
        let (n, bytes) = (want.dropped.files.len(), total(want));
        let mut m = format!(
            "legacy: {n} dropped paths, {bytes} bytes, {}..{}\n\n\
             ADR-0030 Decision 7: every path in the tree of the since commit or of a\n\
             first-parent commit after it that the index no longer tracks, at its last\n\
             content and mode. Rebuilt by mios-gen archive; mios-gen archive --check\n\
             compares it by path, mode and content.\n\n\
             range: {}..{}\npaths: {n}\nbytes: {bytes}\nexcluded: {}\n\n\
             Newest first-parent commit that held each path:\n",
            &h.since[..12],
            &h.head[..12],
            h.since,
            h.head,
            want.dropped.excluded,
        );
        for (path, last) in &want.dropped.files {
            m.push_str(&format!("{} {path}\n", last.commit));
        }
        m
    }

    /// SOURCE_DATE_EPOCH when set, else the since commit's date: a reproducible commit id.
    fn epoch(root: &Path, since: &str) -> Result<String, String> {
        if let Ok(value) = std::env::var("SOURCE_DATE_EPOCH") {
            return value
                .trim()
                .parse::<u64>()
                .map(|v| v.to_string())
                .map_err(|_| format!("SOURCE_DATE_EPOCH {value:?} is not whole seconds"));
        }
        git(root, None, &["show", "-s", "--format=%ct", since], None).map(line)
    }

    /// A fresh bare store holding one commit on the branch, packed into a single pack.
    fn render(
        root: &Path,
        want: &Expected,
        scratch: &Path,
    ) -> Result<(String, Vec<PathBuf>), String> {
        let cfg = &want.cfg;
        let store = scratch.join("store");
        let format = format!("--object-format={}", cfg.object_format);
        let store_arg = store.to_string_lossy().to_string();
        git(
            root,
            None,
            &["init", "--bare", "-q", &format, &store_arg],
            None,
        )?;
        let when = epoch(root, &want.history.since)?;
        let msg = message(want);
        let mut stream = format!(
            "commit refs/heads/{}\nauthor {} {when} +0000\ncommitter {} {when} +0000\ndata {}\n{msg}\n",
            cfg.refname,
            cfg.author,
            cfg.author,
            msg.len()
        )
        .into_bytes();
        for (path, last) in &want.dropped.files {
            let body = &want.bytes[&last.blob.oid];
            let header = format!(
                "M {} inline {}\ndata {}\n",
                last.blob.mode,
                quote(path),
                body.len()
            );
            stream.extend_from_slice(header.as_bytes());
            stream.extend_from_slice(body);
            stream.push(b'\n');
        }
        stream.extend_from_slice(b"done\n");
        let import = ["fast-import", "--quiet", "--done", "--date-format=raw"];
        git(root, Some(&store), &import, Some(&stream))?;
        let repack = [
            "-c",
            "repack.writeBitmaps=false",
            "-c",
            "pack.writeReverseIndex=false",
            "-c",
            "pack.writeBitmapHashCache=false",
            "-c",
            "pack.threads=1",
            "repack",
            "-a",
            "-d",
            "-f",
            "-n",
            "-q",
        ];
        git(root, Some(&store), &repack, None)?;
        git(root, Some(&store), &["prune-packed"], None)?;
        let branch = format!("refs/heads/{}^{{commit}}", cfg.refname);
        let commit = line(git(
            root,
            Some(&store),
            &["rev-parse", "--verify", &branch],
            None,
        )?);
        let packs: Vec<PathBuf> = fs::read_dir(store.join("objects/pack"))
            .map_err(|e| e.to_string())?
            .filter_map(|e| e.ok().map(|e| e.path()))
            .filter(|p| p.is_file())
            .collect();
        let n = packs
            .iter()
            .filter(|p| p.extension().is_some_and(|e| e == "pack"))
            .count();
        if n != 1 {
            return Err(format!("repack left {n} packs, not one"));
        }
        Ok((commit, packs))
    }

    /// Replace the store at the root with exactly HEAD, config, the branch and the pack.
    fn install(root: &Path, cfg: &Config, commit: &str, packs: &[PathBuf]) -> Result<(), String> {
        let store = root.join(&cfg.path);
        if let Ok(meta) = fs::symlink_metadata(&store) {
            if !meta.is_dir() || !store.join("HEAD").is_file() {
                return Err(format!(
                    "{} exists and is not a git store; refusing to replace it",
                    cfg.path
                ));
            }
            fs::remove_dir_all(&store)
                .map_err(|e| format!("removing the old {}: {e}", cfg.path))?;
        }
        let heads = store.join("refs/heads");
        fs::create_dir_all(&heads).map_err(|e| e.to_string())?;
        fs::create_dir_all(store.join("objects/pack")).map_err(|e| e.to_string())?;
        fs::write(store.join("HEAD"), head_text(cfg)).map_err(|e| e.to_string())?;
        fs::write(store.join("config"), config_text(cfg)).map_err(|e| e.to_string())?;
        fs::write(heads.join(&cfg.refname), format!("{commit}\n")).map_err(|e| e.to_string())?;
        for pack in packs {
            let name = pack.file_name().ok_or("a pack file without a name")?;
            fs::copy(pack, store.join("objects/pack").join(name)).map_err(|e| e.to_string())?;
        }
        Ok(())
    }

    /// Findings a fresh build leaves only because the new files are not staged yet.
    fn unstaged(finding: &str) -> bool {
        finding.ends_with("is not tracked; stage it with the rest of the archive")
            || finding.ends_with("is tracked but absent from the worktree")
    }

    pub fn run(root: &Path, check: bool) -> Result<String, Failure> {
        let want = expected(root)?;
        let summary = |verb: &str| {
            format!(
                "{} {verb}: {} dropped paths ({} bytes, {} excluded) on {} for {}..{}; paths, modes and content compared",
                want.cfg.path,
                want.dropped.files.len(),
                total(&want),
                want.dropped.excluded,
                want.cfg.refname,
                &want.history.since[..12],
                &want.history.head[..12],
            )
        };
        let bad = compare(root, &want);
        if check {
            if bad.is_empty() {
                return Ok(summary("holds every dropped file"));
            }
            return Err(Failure::Violations(bad));
        }
        // Rebuilding only to un-stage the same content would churn the pack name.
        if bad.iter().all(|f| unstaged(f)) {
            let verb = if bad.is_empty() {
                "is already current".to_string()
            } else {
                format!("is current; stage {}/ to commit it", want.cfg.path)
            };
            return Ok(summary(&verb));
        }
        let scratch = tempfile::tempdir()
            .map_err(|e| Failure::CannotRun(format!("scratch directory: {e}")))?;
        let (commit, packs) = render(root, &want, scratch.path()).map_err(Failure::CannotRun)?;
        install(root, &want.cfg, &commit, &packs).map_err(Failure::CannotRun)?;
        let left: Vec<String> = compare(root, &want)
            .into_iter()
            .filter(|f| !unstaged(f))
            .collect();
        if !left.is_empty() {
            return Err(Failure::Violations(left));
        }
        Ok(summary(&format!(
            "rebuilt at {} (stage {}/ to commit it)",
            &commit[..12],
            want.cfg.path
        )))
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn globs_cross_slashes_like_the_file_budget() {
            assert!(glob(b"docs/*", b"docs/a/b.md"));
            assert!(glob(b"*.env", b"etc/app/.env"));
            assert!(glob(b"a?c", b"abc"));
            assert!(!glob(b"a?c", b"ac"));
            assert!(!glob(b"docs/*.md", b"usr/docs/x.md"));
            assert!(glob(b"**", b""));
        }

        #[test]
        fn paths_are_quoted_for_fast_import() {
            assert_eq!(quote("a b/c"), "\"a b/c\"");
            assert_eq!(quote("q\"\\\n"), "\"q\\\"\\\\\\012\"");
            assert_eq!(quote("\u{e9}"), "\"\\303\\251\"");
        }

        #[test]
        fn the_store_itself_is_never_dropped() {
            assert!(under(".archive/HEAD", ".archive"));
            assert!(under(".archive", ".archive"));
            assert!(!under(".archived", ".archive"));
        }

        #[test]
        fn ls_tree_records_parse_and_garbage_is_refused() {
            let raw = b"100644 blob 0123456789012345678901234567890123456789\ta b.txt\0\
                        120000 blob 1123456789012345678901234567890123456789\tl\0";
            let tree = parse_tree(raw).unwrap();
            assert_eq!(tree["a b.txt"].mode, "100644");
            assert_eq!(tree["l"].mode, "120000");
            assert!(parse_tree(b"100644 blob xyz\tp\0").is_err());
            assert!(parse_tree(b"no tab here\0").is_err());
        }
    }
}
