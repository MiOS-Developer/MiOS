<!-- AI-hint: Repository entry point for the MiOS system FHS overlay, its SSOT, image build pipeline, local AI interface, deployment shapes, and documented scope. AI-related: /usr/share/mios/mios.toml, /usr/libexec/mios/mios-build-driver, /usr/share/mios/ai/system.md, mios-bootstrap, MiOS-DEV. -->
# MiOS

MiOS (pronounced **MyOS**, “My Operating System”) is an Apache-2.0 research project. Its deliverable is a rebuildable Linux operating system blueprint: a Fedora bootc/OCI image, a local agent runtime, and the source and checks needed to regenerate that image. The [architectural thesis](usr/share/doc/mios/manual/thesis.md) defines four linked pillars: an immutable image, one local AI front door, a repository that mirrors the target root filesystem, and configuration projected from one source of truth.

This is the **system repository**. Its `usr/`, `etc/`, `var/`, and other FHS paths describe the filesystem baked into the image. [mios-bootstrap](https://github.com/mios-dev/mios-bootstrap) owns the interactive installer and operator-editable layer. MiOS-DEV is the build environment; Windows provisioning hands the build to MiOS-DEV.

> MiOS is an active proof of concept. WSL2 and development VM runs are the observed environments described by the [thesis](usr/share/doc/mios/manual/thesis.md). Bare-metal, blade fleet, and edge mesh shapes are design targets under development, not claims of a completed deployment.

## System shape

```text
mios-bootstrap installer + operator selections
    -> Total Root Merge with this FHS overlay
    -> MiOS-DEV build pipeline
    -> bootc/OCI image
    -> WSL2, VM, disk, or installer artifact
    -> bootc upgrade / rollback on a bootc host
```

The image includes a desktop, virtualization and GPU support, and a local AI stack. The build uses [Containerfile](Containerfile) and numbered [automation](automation/) stages. The runtime image owns its static files in `/usr`; `/etc` holds host overrides, and `/var` persists across bootc upgrades. A bootc image is the unit of update and rollback.

The planned MiOS-Metal architecture separates a bare-metal Blade from the MiOS guest. Each Blade owns its boot chain, TPM, NICs, radios, mesh access point, and hardware routing; the MiOS image runs as a NIC-less guest. Fleet roles distinguish services on every Blade, singleton services across Blades, and guest-plane services. See the [MiOS-Metal architecture](usr/share/doc/mios/concepts/mios-metal-architecture.md) for design details and limits. Whole-device VFIO passthrough is the driver-free host GPU path; mediated GPU sharing requires a host driver and explicit opt-in.

## One configuration source

[**`usr/share/mios/mios.toml`**](usr/share/mios/mios.toml) is the singular vendor SSOT for packages, repositories, images, ports, services, identity, build resources, and the shared theme. Operator choices are made through the local [HTML configurator](usr/share/mios/configurator/mios.html) and layered above vendor values. Higher nonempty user values win over host and vendor defaults. Generated files must be projections of the resolved TOML rather than independent settings.

The Windows bootstrap reads `[bootstrap.dev_vm.host_reserve]` for MiOS-DEV resources. Its current default reserves half of physical RAM for Windows, with an 8 GB minimum reserve; the generated WSL setting is recalculated during bootstrap. Terminal colors, fonts, geometry, and application launch behavior likewise derive from the theme and terminal sections of the same TOML.

GTK defaults are projected into the image's `/etc/skel/.config` at build time. Native Flatpak launch refreshes both the caller's GTK configuration and each application's sandbox configuration from the layered SSOT. Modern libadwaita receives CSS custom properties; cursor assets are available through Flatpak's host icon paths. Windows Terminal applies `[theme].opacity` and `unfocused_opacity` to every profile. The default `[theme.tmux].pane_background = "theme"` paints the SSOT background in tmux, including remote clients; setting it to `"terminal"` inherits the client's background. Window transparency is supplied by the terminal client. WSLg manages display scaling; MiOS does not impose a fixed text shrink factor. Restart an already-open app to load its updated startup settings.

The root [`.mios` guide](.mios/README.md) explains workflow dotfolders. They stage sources and generated work; they are not alternate runtime FHS locations.

Native configuration consumers use the shared `mios-resolver` naming registry:
`section.key` projects to `MIOS_SECTION_KEY`. Bash and PowerShell globals are
generated from that same registry. Existing public aliases remain compatibility
inputs; a nonempty canonical input takes precedence, and conflicting legacy
inputs fail with names-only diagnostics. Semantically different settings remain
distinct even when an old alias is ambiguous.

Run `mios-gen names --root .` to inspect naming metadata without configuration
values. Run `mios-gen sync --root . --plan` to validate and inspect the complete
generation pipeline, then `mios-gen sync --root .` to execute it. The ordered
stages live in `[generation.sync]` in the SSOT. The old `tools/sync-generated.sh`
entry point delegates to this native command. Some declared stages still invoke
Python or Bash adapters; completing those Rust ports and verifying installed
Windows and Linux pipelines remain required work.

## Local AI contract

Every OpenAI-compatible client resolves through `MIOS_AI_ENDPOINT`, `MIOS_AI_MODEL`, and `MIOS_AI_KEY`. The supported public shapes include `/v1/chat/completions`, `/v1/responses`, `/v1/embeddings`, and `/v1/models`, with function calls and MCP tools. The [agent contract](usr/share/mios/ai/INDEX.md) and [API reference](usr/share/doc/mios/reference/api.md) describe the local interface.

- `mios-llm-light` is the primary local inference and embeddings lane, with model selection in [`llama-swap.yaml`](usr/share/mios/llamacpp/llama-swap.yaml).
- Heavy GPU inference lanes are gated by the resolved configuration.
- Agent routing and tool work run through agent-pipe and the local gateway; PostgreSQL with pgvector holds durable agent memory.
- MCP exposes tools, while A2A connects agents. Service ports and enablement come from `mios.toml`.

No hosted model account is required for the local runtime. Actual acceleration and enabled services depend on the host hardware and operator selections.

## Global MiOS keybindings

The vendor [`[keybindings]` table](usr/share/mios/mios.toml) defines one action map for MiOS systems, MiOS-DEV, Windows hosts, editors and SSH. `mios-unit-gen keybindings` projects it at build time; native terminal startup resolves the layered SSOT again at runtime. Terminal colors, Oh My Posh separators and fonts come from `[colors]` and `[theme]` in that same SSOT.

| Action | Windows / Hyprland / Sway / GNOME desktop | VS Code / code-server, outside terminal | tmux / mobile SSH |
| --- | --- | --- | --- |
| Open terminal | Ctrl+Alt+Shift+T | Ctrl+B, then T | Ctrl+B, then T |
| Open MiOS AI | Ctrl+Alt+Shift+A | Ctrl+B, then A | Ctrl+B, then A |
| View active agents | Ctrl+Alt+Shift+G | Ctrl+B, then G | Ctrl+B, then G |
| Open system monitor | Ctrl+Alt+Shift+M | Ctrl+B, then M | Ctrl+B, then M |
| Summon MiOS window | Ctrl+Alt+Shift+Space on Windows | — | — |

Press and release **Ctrl+B**, then press one key:

| Key | tmux action |
| --- | --- |
| H / J / K / L | Select pane left / down / up / right |
| S / V | Split into top and bottom / left and right panes |
| N / P | Next / previous window |
| O | Cycle the AI workspace's head and workers; select next pane elsewhere |
| F | Toggle compact / automatic workspace layout |
| W | Choose windows and panes; expand a window with the arrow keys |
| Z | Zoom / restore the active pane |
| Y | Enter copy mode |
| D | Detach; running agents keep their session |
| Tab | Send Shift+Tab to the focused agent |
| B, or Ctrl+B again | Send Ctrl+B through to the application |

The editor actions apply only when its terminal is unfocused. When a terminal is focused, Ctrl+B reaches tmux: chord interception is disabled and the editor sidebar binding passes through. The desktop chords use a separate modifier set, so the compositor does not intercept terminal sequences. Generation rejects duplicate action and utility keys; Windows installation checks existing shortcut registrations before assigning its global hotkeys. Operator extensions and third-party hotkeys still require their own conflict checks.

From an SSH connection with a PTY, run `mios` or `mios terminal` to attach to the native session. In Windows CMD, `mios` enters MiOS; `mios agent NAME` opens a globally installed agent with the combined MiOS-MCP/tmux-mcp configuration. `mios agents` lists the installed catalog. Use `mios ssh user@host` to enter a remote MiOS system. Termius and Blink users need Ctrl, Esc and Tab on their keyboard bar; no function keys or Super key are required. Client fonts control glyph rendering; `[theme.tmux].remote_glyph_mode` and `[theme.prompt].remote_glyph_mode` allow an explicit ASCII projection while retaining the SSOT palette.

See the [mobile SSH and shortcut guide](usr/share/doc/mios/guides/mobile-keybindings.md) and [native terminal / MCP contract](usr/share/doc/mios/mcp-tmux.md) for session separation, message receipts and projection details.

Press **Ctrl+B, then G** to focus the **MiOS Agents** view. Inside an AI workspace it uses the existing monitor pane; it does not add a tab. Run `mios agents --watch` to render the view in the current pane. Compact displays show short labels, distinct identity references, pending counts and pane roles; `mios agents --observe` returns full sanitized identities as JSON, also available through `mios_agent_observe`. Registered relay participants and detected panes are shown separately. A running pane does not prove that its harness reads messages. The view omits credentials, message bodies and terminal contents, and reports a queued message as received only after the recipient acknowledges it.

### Live agent workflows in MiOS Terminal

```bash
mios ai                     # choose a head CLI in the native workspace
mios ai --compact           # monitor beside/above one active agent, by orientation
mios ai codex               # open a named head directly
mios agents --watch         # live participants, panes and message receipts
```

`mios ai` opens a client chooser with a wrapped introduction, installed-client status and navigation hints. Enter a client number or name; `n`/`p` page the list in a small pane, and `q` returns to the themed shell. The default launch uses a compact workspace: landscape places the head or active worker on the left and the live agent monitor on the right; portrait places the monitor above the active agent. Other workers keep running in a separate managed session, outside the human tab list. Repeated launches reuse the same head, including while it is parked. **Ctrl+B, then O** cycles the active head/worker; **Ctrl+B, then F** toggles compact/automatic layout. A sufficiently large viewport shows the head on the left and four worker reservations in a grid on the right. Resizing preserves pane identities and processes. Layout thresholds, pane proportions and menu text resolve from `[mcp.tmux.workspace]` at runtime; image builds install the same native implementation.

Windows native launches use `[terminal]` (80 columns × 20 rows in the vendor SSOT), including AI workspaces. Run `mios-launch.exe MiOS-DEV --action ai` for the centered default window. `--compact` keeps the compact workspace when the viewport grows; it does not change the launch size. Actual monitor work area and DPI determine centering and size limits; fullscreen remains available through Windows Terminal. An SSH client supplies its own viewport and window placement; MiOS adapts the layout to its rows and columns.

Inside this workspace, the head's combined `mios-control` MCP connection binds to its verified desktop pane and claims the four reserved workers. Further workers get visible sub-panes, and nested heads receive separate local slot numbers. Plain CLI commands installed by MiOS use the same native launcher. Outside a native human session, MCP uses private headless servers. Closing a head's MCP connection returns its reservations to empty panes and preserves the human session and other heads' workers. Attached clients share the tmux window's layout; the most recently resized client sets its geometry.

Give the head a task with this coordination contract:

```text
Use the combined mios-control MCP tools for this workload. Register this live
session with mios_agent_register and keep its lease private. Launch the selected
worker CLI in a visible helper pane using mios_tmux_start_and_watch or
mios_tmux_nested_workflow. For ongoing work, use an isolated Git worktree.
Have the worker register, receive its task through mios_agent_send, acknowledge
reading it, and send its findings back through the relay. Read and acknowledge
the reply. Report observed process exits and message receipts separately.
Do not claim delivery or completion from a queued message or a quiet pane.
```

**Ctrl+B, then G** opens the live receipt view; **Ctrl+B, then W** selects the head or worker window. CLI installation and MCP availability do not supply provider login, model inference, or tool permission grants. Each selected harness must support and consume MCP, and its configured model must be reachable. The local inference endpoint and runtime theme are resolved from the layered SSOT.

## Build and installation

### Windows entry

The documented bootstrap entry is:

```text
powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/mios-dev/mios-bootstrap/main/Get-MiOS.ps1 | iex"
```

The installer performs Windows provisioning, fetches the current repositories and full system TOML, and hands the build to MiOS-DEV. It is an interactive system installer that can repartition the selected data disk. Read the [bootstrap installation guide](https://github.com/mios-dev/mios-bootstrap/blob/main/usr/share/doc/mios-bootstrap/guides/bootstrap_install.md) for the current phase model, acknowledgement gate, TOML edit and import flow, monitor, and deployment artifacts.

The bootstrap repository's `field/` component owns live and USB media staging through `MiOS-Field`. Its shared staging implementation validates real OCI manifests, blobs, and filesystem layers before marking large media ready. The retired `cat/` launcher paths are no longer an installation entry.

### Linux development build

From a checkout on a suitable Linux builder:

```bash
git clone https://github.com/mios-dev/MiOS.git
cd MiOS
just preflight
just build
```

[`Justfile`](Justfile) also defines `iso`, `raw`, `qcow2`, `vhdx`, and `wsl2` artifact targets. MiOS-DEV is the canonical environment for build operations in the Windows bootstrap path. `just --list` shows the local targets; [self-build](usr/share/doc/mios/guides/self-build.md) describes their dependencies and outputs.

### Dependencies for self-development

The package SSOT declares the shared compiler and linker tools in `[packages.build-toolchain]`, the image, repository and verification tools in `[packages.self-build]`, and service dependencies in their runtime groups. `requires_sections` composes these groups through the existing package resolver; the development image consumes the same dependency closure. Shared agent Python dependencies remain in the requirements file consumed by its isolated environment.

The default `[packages.self-build].retain_toolchain = true` preserves the tools in the final image. Disabling retention deliberately produces a deployment that needs a separate builder. Package declarations and focused tests establish the requested dependency coverage; a successful full image build and runtime checks are still required to verify a deployed generation.

For an existing bootc-compatible installation, [deployment guidance](usr/share/doc/mios/guides/deploy.md) covers image selection, `bootc switch`, upgrades, and rollback. Do not treat a design target as a verified artifact: check the build log and postchecks for the selected image.

## Repository rules

The 16-law registry in [`mios.toml`](usr/share/mios/mios.toml) and its build gates govern contributions. The first six laws define the core image and runtime boundaries:

1. Static system configuration belongs under `/usr`; `/etc` is for overrides.
2. Persistent `/var` paths are declared through tmpfiles, not created during the image build.
3. Quadlet images are bound into the bootc image and units run without unnecessary privilege.
4. The final image must pass `bootc container lint`.
5. AI clients use the one local OpenAI-compatible endpoint.
6. Operator-tunable values originate in `mios.toml`; generated projections must stay in sync.

This repo owns the system overlay, `Containerfile`, automation, systemd and Quadlet units. The installer repo owns its interactive entry scripts and user-editable layer. Avoid tracking the same runtime file independently in both repositories.

## Documentation

| Start here | Purpose |
| --- | --- |
| [Thesis](usr/share/doc/mios/manual/thesis.md) | Four pillars and observed versus designed scope |
| [Architecture](usr/share/doc/mios/concepts/architecture.md) | System layout and component boundaries |
| [Installation guide](usr/share/doc/mios/manual/ch02-installation-and-deployment.md) | Day-0 and first-boot overview |
| [Engineering guide](usr/share/doc/mios/guides/engineering.md) | Build pipeline conventions |
| [Deploy guide](usr/share/doc/mios/guides/deploy.md) | Image lifecycle |
| [AI contract](usr/share/mios/ai/INDEX.md) | Local agent and endpoint rules |
| [Contributing](CONTRIBUTING.md) | Source and review conventions |
| [Agreements](AGREEMENTS.md) | Project acknowledgement and component attribution |

The version is recorded in [`VERSION`](VERSION). MiOS and its deployment shapes remain under active development. Component licenses and upstream credits are recorded in the [license catalog](usr/share/doc/mios/reference/licenses.md) and [credits](usr/share/doc/mios/reference/credits.md).

## Cloud Integration

Every MiOS image is the same MiOS. A GitHub Codespace, a local devcontainer, a Claude Code cloud session, the MiOS-DEV podman machine and the bootable OCI image all build from this repository's SSOT. The dev image starts `FROM` the same podman machine OS mirror, `ghcr.io/mios-dev/machine-os`.

### GitHub Codespaces and devcontainers

Open this repository in a Codespace, or reopen it in a devcontainer. The container opens `/workspaces`, and on first create it clones every MiOS repository listed in `mios.toml` `[workspace].repos` beside this one: MiOS, mios-bootstrap, -dev-loop and mios-micro. A local checkout opens the same set through [`mios.code-workspace`](mios.code-workspace); both views are projected from `[workspace]` by `tools/sync-dotfiles.py`, and the drift gate fails if either differs.

### Claude Code cloud environment

A Claude Code cloud session runs on a fixed Ubuntu VM. MiOS builds its dev image there with rootful podman from `main` and installs `mios-dev`, which enters that image with the session's checkouts mounted at the same paths. It also installs the dev-loop plugin. In the environment's settings (environment menu, **Edit**), set the network to **Full** and paste:

Setup script:

```bash
#!/bin/bash
curl -fsSL https://raw.githubusercontent.com/mios-dev/MiOS/main/.devcontainer/cloud-shell/claude-code-cloud.sh -o /tmp/mios-claude-code-cloud.sh && bash /tmp/mios-claude-code-cloud.sh
exit 0
```

Environment variables:

```
CLAUDE_CODE_PLUGIN_DIRS=/opt/dev-loop
```

The script is [`.devcontainer/cloud-shell/claude-code-cloud.sh`](.devcontainer/cloud-shell/claude-code-cloud.sh). Every `FEDORA_*` value has a default there, so set one only to override it. The setup script always exits 0. A cold first build can run past the setup budget: the devcontainer lifecycle is then deferred, `mios-dev` reports it, and `bash /opt/dev-loop-fedora/cloud-fedora-setup.sh --lifecycle` applies it. Details: [cloud-shell README](.devcontainer/cloud-shell/README.md).

A session's first prompt for live debugging:

```
/dev-loop:goal Develop MiOS live in this cloud session. Run every gate inside the MiOS dev image (`mios-dev <cmd>`, same $PWD): tests/run-suites.sh lint, python3 tools/ci-suites.py --check, python3 tools/sync-bootstrap.py --check. Take the highest-value open task from the MiOS task list, reproduce its failure, fix it in code, prove it with a positive and a negative control, and push to main. Repeat until the task list's acceptance criteria hold.
```

### Codex Cloud environment

Create or edit a Codex Cloud environment and paste the following blocks into the matching fields. The [official environment guide](https://learn.chatgpt.com/docs/environments/cloud-environments) describes **Install script**, **Start skill**, network access, secrets and publishing. This setup runs the canonical Fedora MiOS devcontainer through Podman; it does not replace the cloud provider's host kernel or turn that host into a booted bootc system. A cloud host must permit Podman containers. If it does not, installation fails and that environment cannot serve as this MiOS builder.

**Environment name:**

```text
MiOS
```

**Repositories:** select these repositories in the editor. The first four are the public workspace catalog in `[workspace].repos`; select the private credential repository only when this environment is authorized to access it.

```text
mios-dev/MiOS
mios-dev/mios-bootstrap
mios-dev/-dev-loop
mios-dev/mios-micro
mios-dev/.secrets
```

**Install script:** paste the entire block. It uses the checked-out version when available, otherwise the published `main` script. Keep failures visible; do not append `exit 0`.

```bash
#!/usr/bin/env bash
set -euo pipefail
repo="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -n "$repo" && -f "$repo/.devcontainer/cloud-shell/codex-cloud.sh" ]]; then
    export MIOS_CLOUD_ROOT="$repo"
    bash "$repo/.devcontainer/cloud-shell/codex-cloud.sh" install
else
    curl -fsSL https://raw.githubusercontent.com/mios-dev/MiOS/main/.devcontainer/cloud-shell/codex-cloud.sh -o /tmp/mios-codex-cloud.sh
    bash /tmp/mios-codex-cloud.sh install
fi
```

The [versioned installer](.devcontainer/cloud-shell/codex-cloud.sh) applies `.devcontainer/devcontainer.json`, including its features and create/start lifecycle. Packages resolve from `[packages.devcontainer]` and its dependency closure, including `[packages.self-build]`, `[packages.mcp]` and `[packages.agent_cli]`. Global agent binaries come from `[agent_cli].tools`: Claude Code, Codex, Gemini, Copilot, OpenCode, Antigravity and Aider. The same image supplies the native MiOS dispatcher, combined MiOS-MCP/tmux-mcp server, session relay, tmux, Oh My Posh, fonts, keybindings, Rust toolchain, image tools and Python environments. It renders terminal and prompt configuration from the layered SSOT on startup. CLI installation does not authenticate a provider account; Aider is a worker CLI and has no native MCP client.

**Start skill:** this field takes instructions, not a shell script. Paste:

```text
Start this task in the MiOS Fedora development environment.

1. Find the checked-out MiOS system repository (it contains usr/share/mios/mios.toml). If needed, set MIOS_CLOUD_ROOT to its absolute host path. Run /usr/local/libexec/mios-codex-cloud start, then /usr/local/libexec/mios-codex-cloud check. Stop and report the actual failure if either fails; do not continue on the cloud host as though it were MiOS.
2. Execute all repository, build, test and agent commands through mios-dev <command>. The wrapper runs as the devcontainer user. Selected public sibling repositories are mounted beneath /workspaces; the primary checkout is /workspaces/MiOS. Verify mios-dev id, mios-dev cat /etc/os-release, mios-dev mios agents and mios-dev mios-agent-pipe-dev check before making changes.
3. Read AGENTS.md and the repository's task list inside MiOS. Resolve packages, ports, tool paths, themes, fonts and shortcuts through usr/share/mios/mios.toml and its host/user layers. Re-render projections; do not maintain an independent cloud palette or package list.
4. For an interactive terminal, run mios-dev mios terminal. For an agent head, run mios-dev mios agent NAME with a supported authenticated MCP-capable CLI. Use its combined mios-control connection for native tmux worker panes, system tools and messages. Keep providers' credentials in approved private credential stores; do not copy them into images or publish logs containing them.
5. Register each participating running session with mios_agent_register and retain its lease privately. Discover peers with mios_agent_list. Use mios_agent_send, mios_agent_receive and mios_agent_ack for addressed messages. Poll the inbox at task boundaries. Queued messages, system_status and A2A peer cards are not evidence that another CLI or desktop chat received a message. Report delivery only after the addressed recipient acknowledges it; report completion only after a substantive reply.
6. Verify each change with a passing candidate and a planted negative control that fails for the intended reason. Preserve the working tree and unrelated changes. Use separate worktrees for concurrent workers. Run required repository gates inside MiOS and report results, limitations and remaining work accurately.
```

**Internet access:** enable **Allow Codex to access internet**, choose **Package managers**, and paste these additional build destinations into **Additional allowed domains**. Fedora mirrors and registry download redirects may need additional domains; a denied destination must be added explicitly and the failed step retried.

```text
github.com
api.github.com
raw.githubusercontent.com
objects.githubusercontent.com
release-assets.githubusercontent.com
codeload.github.com
ghcr.io
pkg-containers.githubusercontent.com
quay.io
cdn.quay.io
registry.fedoraproject.org
mirrors.fedoraproject.org
download.fedoraproject.org
dl.fedoraproject.org
static.rust-lang.org
sh.rustup.rs
crates.io
index.crates.io
static.crates.io
registry.npmjs.org
pypi.org
files.pythonhosted.org
astral.sh
antigravity.google
```

Provider inference destinations depend on the accounts and SSOT endpoints you actually use; allow those separately. Do not paste the localhost's loopback URL into cloud settings and expect it to reach MiOS-Xbox.

**Environment variables:** add the following non-secret entry. Source and image defaults are handled by the installer; set `MIOS_CLOUD_ROOT` only if checkout discovery needs an explicit absolute path. Ports and theme values stay in TOML.

```text
PYTHONUNBUFFERED=1
```

**Network secrets:** use **Manage** to attach only the private registry or provider credentials required for the task, scoped to their destinations. Leave it empty for public dependency installation. Do not paste API keys, OAuth tokens or `.secrets` contents into these README blocks or ordinary environment variables. Private credential files and proxy secrets are different mechanisms; provision the required approved credential reference before claiming a CLI can use an account.

**Privacy / Who can use:**

```text
Only me
```

**Advanced:**

```text
VPN: none for public builds; attach an authorized connection only for private MiOS services.
OIDC: none for public builds; attach a scoped identity only when a private registry or deployment requires it.
```

Use the configured private networking path to connect cloud sessions to MiOS-Xbox or another MiOS host. Keep the local MCP server on stdio and local sockets; remote tmux access goes through SSH. The private `.secrets` repository is not an image layer, a public build dependency or a source of automatic credentials.

**Validation before publishing:** run the installation and Start skill in the environment editor. Verify Fedora userspace, all catalog CLIs, the projected prompt/tmux theme, gateway readiness, MCP tool/resource preservation and a real two-session message/acknowledgement. Save a draft if a check fails; publish only a verified environment. These blocks configure an environment when pasted and run; this README edit alone does not create or publish one. See [cloud setup details](.devcontainer/cloud-shell/README.md).

The image equivalence work is still in progress. One Containerfile with one stage per profile, plus a gate that proves every image carries the same floor, is tracked as task_3457 and task_3463 to task_3474.
