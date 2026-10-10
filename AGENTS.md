<!-- AI-hint: The primary entry point and source of truth for all AI agents in mios-bootstrap — the interactive installer + user-editable layer of MiOS. Defines the project identity, the whole-system context (immutable bootc/OCI Fedora workstation that is also a local self-replicating agentic AI OS), OpenAI-compatible interface standards, and this repo's role as the user-facing installer surface that drives the build pipeline into a deployed image.
     AI-related: /etc/mios/profile.toml, /usr/share/mios/profile.toml, /usr/share/mios/ai/system.md, /etc/mios/ai/system-prompt.md, /usr/share/mios/ai/, /usr/share/mios/mios.toml, /usr/share/mios/llamacpp/llama-swap.yaml, /etc/mios/install.env, mios-bootstrap, mios-dev, mios-pipeline, mios-pull, mios-llm-light, mios-pgvector -->
# AGENTS.md

> Canonical agent entry point for `mios-bootstrap.git` — the interactive
> installer + user-editable layer for MiOS. Follows the [agents.md][1]
> standard and is the SSOT for any agent CLI that arrives at this repo.
> Per-tool stubs (`CLAUDE.md`, `GEMINI.md`, `system-prompt.md`) are thin
> redirectors that defer here.
>
> **Strict OpenAI API standards and patterns ONLY.** Every interface is
> OpenAI-API-compatible verb-for-verb. No vendor-native protocols, no
> proprietary side-channels, no fallback to vendor-cloud URLs, no
> vendor-specific agent / dev-tool product references in any AI file.
>
> **Universal Harness Neutrality & Native MiOS-MCP + tmux-mcp (Globally Binding):**
> No agent or harness (Antigravity, OpenAI Codex, Claude Code, OpenCode, Gemini, etc.)
> is permanently hardcoded as the master, worker, or monitor. Absolutely **ANY AGENT** can be
> dynamically promoted to Orchestrator or Monitor based on whichever Agent CLI is invoked.
> All multi-agent workflows, inter-agent coordination, sub-pane spawning, command execution,
> and live monitoring MUST use `MiOS-MCP` (`agent-pipe` / `agent-relay` / `state.json`) and `tmux-mcp v2`
> native slot tools (`open-pane`, `execute-command`, `send-keys`, `capture-pane`, `start-and-watch`,
> the MiOS `mios_tmux_nested_workflow` adapter) natively. Native heads use verified human panes;
> unbound automation uses private headless sessions. Each participant registers and consumes
> its own relay inbox; automatic coordinator takeover and desktop conversation transfer are
> not implemented. See [.agents/COORDINATION.md](.agents/COORDINATION.md).
>
> **System repo:** <https://github.com/mios-dev/mios> — that's where
> the FHS overlay, Containerfile, automation scripts, and the six
> Architectural Laws live. This repo is the *user-facing entry surface*.
>
> **Architecture Decision Records (ADRs):** See [ADR.md](ADR.md) for the index of system decision records.
>
> [1]: https://agents.md

## 0. What MiOS is (so this repo's job makes sense)

MiOS is one thing built two ways at once: an **immutable, bootc/OCI-shaped
Fedora workstation** (the whole OS is a single container image — boot it,
`bootc upgrade` it like a `git pull`, `bootc rollback` it like a Ctrl-Z) that
is *also* a **local, self-replicating, agentic AI operating system**. The same
image that ships GNOME/Wayland, NVIDIA + ROCm + Intel iGPU via CDI, KVM/libvirt
with VFIO passthrough, and a k3s + Ceph one-node-cluster path also ships a full
local agent stack behind one OpenAI-compatible endpoint. The OS can reason about
itself, drive its own tools, and — because the whole thing is one rebuildable
OCI image — effectively re-create itself.

The system's lifecycle is a single throughline: **installer (this repo) →
build pipeline → OCI image → bootc lifecycle on the host.** `mios.git` is the
FHS overlay that gets baked into the image; `mios-bootstrap.git` (this repo) is
the user-facing entry surface that captures the operator's choices, performs the
Total Root Merge, drives the build, and hands a deployed, self-developing host
back to the operator. Everything below describes *this repo's* slice of that
whole: how a paste on Windows or a `curl | bash` on Linux becomes a booted,
agentic MiOS host that can then rebuild itself.

### Five Load-Bearing Architectural Invariants (Corrections)
To ensure agents reason correctly about the system substrate, the following five corrections to standard draft assumptions must be strictly adhered to:
1. **`/var` Persists by Default**: On bootc/ostree systems, `/var` is a persistent location rather than a volatile tmpfs. This guarantees continuity for large models, database state, and virtual machine disks.
2. **Unified Kernel Image (UKI) vs MOK Conflation**: The bootloader and kernel signing chain is a Unified Kernel Image (`shim -> systemd-boot -> signed UKI`) where kernel command line parameters (kargs) are baked and signed into the UKI itself. This is distinct from Machine Owner Key (MOK) module signing, which only governs out-of-tree runtime driver signature checks.
3. **Graphics Virtualization (venus vs CUDA)**: The `venus` VirtIO GPU protocol is strictly a graphics/Vulkan transport. It does not support CUDA execution inside a microVM; running CUDA workloads inside a virtualized guest requires whole-device VFIO hardware passthrough.
4. **GPU Fractioning / mediated vGPU Limit**: GPU fractioning (using `mdevctl` or SR-IOV) is technically impossible without loading a physical host-side PF driver. Therefore, on a driver-free host, the only supported option is whole-device passthrough via `vfio-pci`; vGPU fractioning requires an explicit, invariant-breaking opt-in.
5. **The Blade owns the hardware; the MiOS image is a guest obfuscated from it.** A MiOS-Metal (formerly Mini) Blade is *bare metal*: it owns the NICs, radios, TPM, boot chain and dGPUs, and hosts the MiOS OCI image as a **NIC-less guest** that is obfuscated from all of it. A fleet is 2-6 Blades, and **every Blade is its own AP forming one mesh Wi-Fi**, as well as a member of the **HCI mesh VPN cluster** that the hyper-converged lanes (Ceph, k3s, Pacemaker) actually run over. **No hosted node is ever an access point** -- hardware-facing roles live on the Blade and must be *unclaimable* by a guest archetype, because a capability the guest plane can express but can never serve reads as available and fails at the hardware boundary. This yields three role shapes the SSOT must distinguish: **universal-per-Blade** (radio, mesh membership), **singleton-across-Blades** (WAN gateway, mesh coordinator), and **guest-plane** (k3s, Pacemaker). See `TASKS.md` task_929..task_935.

## 1. Repo identity

* **Project:** MiOS — pronounced *MyOS* (short for *My OS*). Research project,
  Apache-2.0. Generative: synthesized from seed scripts + curated docs, then
  expanded under human review.
* **Role:** interactive installer (Phase 0..4) and user-editable layer
  of the three-layer profile model (vendor < host < user). The *entry surface*
  for the build-pipeline → image → bootc lifecycle.
* **Version:** see `VERSION` (top-level).
* **Owns:** AI files (`usr/share/mios/ai/`), knowledge graphs, user
  profile templates, installer scripts (`Get-MiOS.ps1`,
  `bootstrap.{sh,ps1}`, `install.{sh,ps1}`, `build-mios.{sh,ps1}`,
  `seed-merge.{sh,ps1}`).
* **Does NOT own:** `Containerfile`, FHS system overlay, systemd units,
  Quadlet sidecars, kernel args, tmpfiles, sysusers — those live in
  `mios.git`. **Never double-track paths across the two repos.**

## 2. The three project-wide laws (this repo's slice)

These three are the installer-repo restatement of the system contract. The
full **six Architectural Laws** (USR-OVER-ETC, NO-MKDIR-IN-VAR, BOUND-IMAGES,
BOOTC-CONTAINER-LINT, UNIFIED-AI-REDIRECTS, UNPRIVILEGED-QUADLETS) are enforced
at build/lint time in `mios.git`; see §11 for Law 5, the one this layer touches
directly.

1. **Native Linux FHS folder structuring.** Files live where the
   Filesystem Hierarchy Standard says they live. Bootstrap files
   mirror those destinations even at this repo's root. (Aligns with the
   system's USR-OVER-ETC / NO-MKDIR-IN-VAR laws — static config in `/usr`,
   `/etc` for overrides only, `/var` declared via tmpfiles.)
2. **OpenAI API standards FULLY.** Every agent / model / tool surface
   is OpenAI-API-compatible: `/v1/chat/completions`, `/v1/responses`,
   `/v1/embeddings`, `/v1/models`, function-calling, structured
   outputs, MCP via the Responses API. **No vendor-specific
   agent / dev-tool product references in any AI file.** (This is the
   user-facing face of the system's UNIFIED-AI-REDIRECTS law.)
3. **MiOS is a root filesystem overlay; `.git` IS `/`.** Bootstrap is
   what *makes* `.git` equal `/` on a target host. The Total Root
   Merge in Phase-1 clones `mios.git` into `/` and overlays this
   repo's `etc/`, `usr/`, `var/` on top. The next boot IS the edit — the
   premise that makes MiOS self-developing.
   - **Dual structure (`.git/root` is both Linux FHS and AI training image shape):**
     The repo root overlay satisfies standard POSIX Linux FHS conventions
     (`usr/`, `etc/`, `var/`, `srv/`) while simultaneously structured as an
     ingestible, high-density AI training corpus dataset. Every source file
     carries structured machine-readable headers (`AI-hint:`, `AI-doc:`,
     `AI-related:`), the manual suite (`usr/share/doc/mios/manual/ch01..ch88.md`)
     serves as the authoritative grounded knowledge corpus, and the corpus ledger
     (`usr/share/mios/reference/manual-corpus.tsv`) guarantees full provenance
     and zero fabrication drift for local SFT/LoRA fine-tuning
     (`mios-finetune-dataset`) and continuous epistemic learning (`/learn`).

### Root dotfolder control plane

Root-level dotfolders are workflow control-plane directories, not alternate
runtime FHS destinations. The contract is documented in [`.mios/README.md`](.mios/README.md):

* `.prompts/` organizes prompt sources and indexes; shipped prompt files land
  under `usr/share/mios/prompts/`.
* `.mios/system-prompt.md` is the copy/paste control prompt for external
  research/chat applications, with separate auth-profile blocks.
* `.dotfiles/` documents the bootstrap-owned non-secret operator dotfiles
  layer; it may contain layout and `secret_ref` metadata, never credential
  values.
* `.research/` stages evidence; durable research lands under `docs/research/`.
* `.docs/` stages documentation; shipped documentation lands under
  `usr/share/doc/mios/`.
* `.secrets/` contains only its tracked README; its contents are ignored and
  must never contain real credentials.
* `.artifacts/` and `.work/` are disposable generated/scratch paths and are
  ignored.

Do not move FHS-owned files into dotfolders merely for organization. A
dotfolder source is promoted through the existing SSOT, projection, review,
and drift-gate workflow before it becomes a deployable artifact.

## 3. `mios.toml` is THE singular SSOT

**`mios.toml` is the singular file that runs the entire pipeline.** It
is the **library of every verb, variable, and value** the codebase
consumes — packages, ports, AI inference lanes, services, agent behaviour,
identity, theme. Edited as HTML in a local browser by the defined user, saved
locally, and fetched by the pipeline. This is how an operator's choices reach
every downstream step of the build → image → bootc chain without a single
hardcoded literal.

### What the TOML carries (inline)

* **Packages** — RPMs, Flatpaks, OCI images, layered package sets per
  deployment shape (`[packages.<section>].pkgs`)
* **Dependencies** — every transitive requirement
* **Repositories** — GitHub remotes, local Forgejo URL, OCI registries,
  upstream git mirrors
* **Applications** — every layered Quadlet container, every Flatpak
  desktop app, every native window app
* **Tools** — CLI surfaces, helper scripts, dev tools
* **Settings** — every operator-tunable knob across the entire stack
* **Username / Linux account** — uid 1000 `mios` user, full credentials
  pipeline (`[identity]`, `[auth]`)
* **Color palette** — globally applied, **platform-agnostically**,
  across every terminal and console (Windows Terminal, conhost,
  GNOME Terminal, tmux, MOTD, fastfetch, oh-my-posh, dashboard
  borders, configurator HTML `:root`)
* **Extras / bloat / optional** — operator-toggled add-ons
* **Passwords + credentials** — operator-set, persisted only in the
  TOML overlay (never round-tripped through `install.env`'s readable
  bridge for secret keys)
* **Quadlets** — `[quadlets.enable]` table + per-Quadlet parameters
* **User preferences** — theme, terminal dims (80×40), locale,
  keyboard, timezone

### The edit-save-fetch lifecycle

1. **Edit:** operator opens `mios.html` in a **local browser**.
   No server, no extension, no install step. `file://` is fine.
2. **Save:** the configurator writes the updated TOML to disk.
   On Windows: `%USERPROFILE%\Downloads\mios.toml` (browsers can't
   write back to `file://`). On Linux: in-place to
   `~/.config/mios/mios.toml`.
3. **Fetch:** the pipeline (`mios build` on Windows, `mios-pipeline.sh`
   on Linux) reads the TOML from the layered overlay and uses it to
   drive every downstream step.
4. **Overlay/install:** TOML selections bake into the overlay
   **before** installation. No mid-install prompts that bypass the
   TOML.

The Windows `mios build` verb specifically promotes the newest
`mios*.toml` from `%USERPROFILE%\Downloads` to `M:\etc\mios` +
`M:\usr\share\mios` and archives the source as
`.imported-<timestamp>` BEFORE running `mios-pull --hard` so operator
edits aren't clobbered.

### Resolution layers (highest first)

```
~/.config/mios/profile.toml      # per-user (highest, seeded from /etc/skel)
/etc/mios/profile.toml           # host
/usr/share/mios/profile.toml     # vendor (lowest, always present)
```

`install.sh:resolve_profile_layers` walks all three at install time
and field-level overlays them into the runtime defaults. **User-set
fields in higher layers win. Empty strings do NOT override non-empty
values below them.**

**Empty / missing user TOML is the vendor-default state, not an error.**
**Hardcoded values that could live in `mios.toml` are bugs** — lift
them, expose them in the HTML configurator, then read them from the
layered overlay.

## 4-8. Installation, Day-0/N, Build Phases, & Artifact Matrix

Detailed installation processes, bootstrap build phases, terminal profile setups, self-development loops, and artifact matrix specifications have been moved to the companion guide:
- See [bootstrap_install.md (mios-bootstrap)](https://github.com/mios-dev/mios-bootstrap/blob/main/usr/share/doc/mios-bootstrap/guides/bootstrap_install.md) for full setup instructions.

## 9. MiOS-DEV ≡ MiOS

MiOS-DEV is the **source upon which MiOS itself is based** — testbed
AND substrate. It mirrors the layered Quadlet container surface that
ships in production MiOS, so the build pipeline's tests and the
self-development workflow have the full runtime surface available.
Representative Quadlet units under `usr/share/containers/systemd/`:

* `mios-llm-light` — the **primary** local inference lane (llama.cpp behind
  the `llama-swap` proxy image, port key `llm_light`; also serves embeddings via
  `nomic-embed-text`)
* `mios-llm-heavy` / `mios-llm-heavy-alt` — gated heavy GPU lanes (vLLM on
  port key `vllm`, served-name `mios-heavy`; SGLang alternate on `sglang`). Off by default on VRAM
  grounds
* `mios-pgvector` — PostgreSQL + pgvector, the unified agent datastore
  (port key `pgvector`)
* `mios-open-webui` — Open WebUI browser front-end (port key `open_webui`)
* `mios-searxng` — SearXNG metasearch backing `web_search` (port key `searxng`)
* `mios-guacamole` (with `mios-guacd`, `mios-guacamole-postgres`) — browser
  desktop
* `mios-forge` / `mios-forgejo-runner` — local git forge + CI runner
* `mios-cockpit-link`, `mios-code-server`, the `mios-webtools-*` pod,
  `mios-adguard`, `mios-crowdsec-dashboard`, and the `mios-k3s` / `mios-ceph`
  cluster path
* (every Quadlet under `usr/share/containers/systemd/`)

The MiOS-Hermes gateway (`hermes`), the agent-pipe orchestrator (`agent_pipe`), the
delegation prefilter (`prefilter`), and the opencode `/v1` gateway (`opencode_gateway`) run as
service units alongside these containers (see §11). MiOS-DEV needs the `mios`
user appended (uid 1000, the same login user the production image ships) so the
same per-user configs and rootless podman behaviors carry across.

## 10. Loading order (system prompt)

This file (AGENTS.md) is the agents.md-standard repo entry. The runtime
LLM system prompt is `/usr/share/mios/ai/system.md`. Bootstrap deploys
this repo's `system-prompt.md` to `/etc/mios/ai/system-prompt.md`; the
local agent stack loads it for chat completions.

1. `~/.config/mios/system-prompt.md` — per-user override
2. `/etc/mios/ai/system-prompt.md` — host/admin override (deployed by
   bootstrap)
3. `/usr/share/mios/ai/system.md` — vendor canonical (lowest, from
   `mios.git`)

## 11. Endpoint contract (OpenAI-compatible)

Architectural Law 5 (**UNIFIED-AI-REDIRECTS**) — every OpenAI-API-shaped
client on the system resolves through `MIOS_AI_ENDPOINT`,
`MIOS_AI_MODEL`, `MIOS_AI_KEY`.
**No vendor-cloud URLs. No vendor-specific agent / dev-tool product names
anywhere.** This is what lets any OpenAI-API-compatible editor/CLI client
talk to the same local brain with no vendor lock-in.

Behind that one endpoint is the local agent stack (verify ports against the
units / `mios.toml`):

* **agent-pipe** (port key `agent_pipe`) — standalone orchestrator: router + refine +
  council/swarm fan-out + critic/polish; fronts Hermes for every gateway.
* **MiOS-Hermes** (port key `hermes`) — OpenAI-compatible agent gateway: sessions,
  tool-loop, skills, browser/CDP control.
* **mios-gateway-agent** (port key `hermes`) — Next-generation OpenAI-compatible agent gateway replacing Hermes (phase 2, disabled until task_83).
* **prefilter** (port key `prefilter`) — injects fan-out hints on decomposable prompts,
  forwards to Hermes.
* **mios-llm-light** (port key `llm_light`) — **primary** inference lane: llama.cpp behind
  the upstream `llama-swap` proxy image (`ghcr.io/mostlygeek/llama-swap`),
  multi-model auto-swap + KV-cache paging; serves everyday models, the
  `mios-opencode` coder model, **and embeddings** (`nomic-embed-text`,
  OpenAI-compat `/v1/embeddings`). Model map:
  `usr/share/mios/llamacpp/llama-swap.yaml`.
* **mios-llm-heavy** (port key `vllm`, served-name `mios-heavy`) / **mios-llm-heavy-alt**
  (port key `sglang`) — gated heavy GPU lanes (vLLM / SGLang), off by default on VRAM grounds.
* **opencode-gateway** (port key `opencode_gateway`) — opencode → OpenAI `/v1` shim; a real `/v1`
  council peer (loopback).
* **OWUI** (port key `open_webui`) — Open WebUI front-end; **SearXNG** (port key `searxng`) backs
  `web_search`.
* **pgvector** (`:5432`) — PostgreSQL + pgvector, the unified agent datastore
  (agent memory, events, tool calls, sessions, skills, scratch, knowledge with
  vector recall, …).

The engines speak the OpenAI/Ollama-compatible API, so any OpenAI-API client
talks to them unchanged — `llama-swap` and that wire-compat API are the only
legitimate upstream references; the MiOS *unit identity* is `mios-llm-light`.
The throughline: **inference lanes → agent-pipe/Hermes orchestration →
pgvector memory → MCP (tools) / A2A (agents)**, all behind `MIOS_AI_ENDPOINT`.

## 12. Setup commands

See [bootstrap_install.md (mios-bootstrap)](https://github.com/mios-dev/mios-bootstrap/blob/main/usr/share/doc/mios-bootstrap/guides/bootstrap_install.md) for Windows and Linux setup/bootstrap command references.

## 13. Operating rules for agents

* **cwd:** `/` IS the deployed system root. Bootstrap repo files map
  to FHS destinations (`etc/` → `/etc/`, `usr/` → `/usr/`, etc.).
* **Deliverables:** complete replacement files only. No diffs, no
  patches, no `# ... rest unchanged ...` placeholders.
* **Tone:** direct, technical, no hedging qualifiers, no emoji unless
  the user asked. Ground every suggestion in a concrete FHS path with
  file:line.
* **OpenAI-API-only.** Never reference vendor-specific agent CLIs,
  dev-tool products, or cloud-AI URLs in MiOS docs / code / commit
  messages. The OpenAI public API surface is the only addressable
  contract.
* **TOML-first.** Before adding a constant to a script, check whether
  the value is operator-tunable. If yes, add it to `mios.toml`,
  expose it in the HTML configurator, then read it from the layered
  overlay.
* **Confirm before:** `git push`, `bootc switch`, `bootc upgrade`,
  `dnf install`, `systemctl daemon-reload`, `rm -rf` (especially against
  `.git` or working tree), `git reset --hard`, `git clean -fd`,
  `wsl --unregister`, `podman machine rm`, `Remove-Partition`,
  `Disable-WindowsOptionalFeature`.
* **MiOS-DEV is THE builder.** ALL build operations (`podman build`,
  BIB, `bootc switch`, manifest gen) run **inside** `podman-MiOS-DEV`.
  Windows side is provisioning + handoff ONLY. Don't write commit
  messages or comments that contradict this.
* **Latest packages and software.** Default to newest stable upstream
  when pinning RPMs / OCI tags / binaries / base images. Bump
  conservative pins forward on next touch unless held for a
  documented reason.
* **Every repo file is tracked, whitelisted, and pushed.** When
  generating any artifact in `mios` / `mios-bootstrap`, add a
  `.gitignore` whitelist line, stage, commit, push. Pulling latest
  must restore full context.
* **No double-tracking.** `mios.git` owns the system FHS overlay;
  `mios-bootstrap.git` owns the user-facing installer. Never
  cross-track paths.
* **Rust static binaries, globally.** MiOS runs from a refined, canned,
  minified code base compiled to **Rust static binaries**. Law 14
  (TARGET-LANGUAGES) is the floor -- it grandfathers what already exists --
  and this is the destination: *all* of it converts. When adding or porting a
  generator, gate, verb backend or service, reach for a Rust static binary in
  `tools/native/` rather than another Python or bash script.
* **"Canned" means hardened templates.** Code and scripts are kept as
  **hardened templates an AI generates the next tool FROM**. That is Law 16
  (ONE-TEMPLATE-PER-TYPE) read as a *generative* system rather than only a
  conformance gate: one canonical template per file type under
  `usr/share/mios/templates/`, declared in `[templates.<type>]`, scaffolded
  strictly via `mios new <type>` / `miosd scaffold`. Do not hand-roll a new
  file of a type that already has a template.
* **OpenAI-format schemas, globally.** Every schema is an OpenAI format --
  not only the `/v1` wire surface but schemas everywhere. The strict-schema
  converter already in the tree (deeply-nested JSON Schema to strict OpenAI
  schema) is the shape to standardise on.

## 14. Persistence sanitization

**MiOS names apply globally to new working artifacts.** Use a sanitized,
function-based `mios-<purpose>` stem for worktree directories, branches,
verification directories and helper artifacts. Follow the lowercase
`a-z0-9-` charset in `[variants.naming]`; identify the job, not a harness,
operator, model or timestamp. Keep ownership and run timestamps in the relay
and ledger. Check for collisions before creation; do not silently overwrite
an existing path. Rename owned lanes with Git worktree operations, preserve
their commits and uncommitted contributions, and update executable references.
Retain historical receipts and other participants' identities and worktrees.

Anything persisted to `/var/lib/mios/ai/memory/` or
`/var/lib/mios/ai/scratch/` must be vendor-neutral:

* Strip vendor-specific names (model names, organization names,
  product names) unless the user asked for them.
* Drop chat metadata (`user-id`, `session-id`, `conversation-id`).
* Reduce all paths to FHS canonicals; resolve symlinks before writing.
* Never persist secrets (PATs, API keys, passphrases). If a tool call
  returned one in a previous turn, redact it before saving.

> The durable agent datastore is **PostgreSQL + pgvector** (`mios-pgvector`,
> `:5432`) — these on-disk paths are the lightweight episodic/scratch journals,
> not the primary store.

## 15-16. File layouts & Persistent paths

See [bootstrap_install.md (mios-bootstrap)](https://github.com/mios-dev/mios-bootstrap/blob/main/usr/share/doc/mios-bootstrap/guides/bootstrap_install.md) for full descriptions of file mappings in this repo and paths persisted on a deployed host.

## 17. Failure mode

When a question is outside MiOS scope or the data isn't available
locally, say so explicitly:

> *"I don't have that on this host; check `<concrete file or URL>`."*

Don't fabricate FHS paths. Don't invent endpoint URLs. **Don't name
vendor-specific agent or dev-tool products.** If unsure between two
valid sources, name both and let the operator choose.
