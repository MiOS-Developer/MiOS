<!-- AI-hint: Claude Code entry point for mios.git — the MiOS SYSTEM repo (the OCI/bootc image source), where the repo root IS the deployed system root. Use this to navigate the build pipeline (Containerfile -> automation/NN-*.sh), the mios.toml SSOT, the six architectural laws, the local AI plane, and the `just` build surface. Defers to AGENTS.md / /usr/share/mios/ai/system.md for runtime agent identity.
     AI-related: usr/share/mios/mios.toml, usr/share/mios/ai/system.md, usr/share/mios/ai/INDEX.md, automation/lib/packages.sh, automation/99-postcheck.sh, usr/lib/bootc/kargs.d/, usr/share/mios/llamacpp/mios-llm-light.yaml, MIOS_AI_ENDPOINT -->
# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> Canonical agent prompt: `AGENTS.md` (this repo's agents.md-standard entry) → `/usr/share/mios/ai/system.md` (deployed vendor canonical). This file covers only the Claude-Code-specific deltas for working on the source tree.

## What this repo is

This is **`mios.git`** (`github.com/mios-dev/MiOS`) — the **system FHS overlay** of MiOS, an immutable, `bootc`/OCI-shaped Fedora workstation that is *also* a local, self-hosted agentic AI OS. The entire OS is one rebuildable container image (`ghcr.io/mios-dev/mios:latest`): boot it, `bootc upgrade` it like a `git pull`, `bootc rollback` it like a Ctrl-Z.

**The repo root IS the deployed system root.** `usr/`, `etc/`, `srv/`, `var/` here land at exactly those paths on a booted host — no `system_files/` indirection, no Ansible. Browse `usr/` on GitHub and you're looking at `/usr` on the machine. When you edit a file here you are editing the OS.

This repo defines *what the OS is*. The sibling **`mios-bootstrap.git`** (interactive installer + user-editable overlay; also cloned locally at `C:\mios-bootstrap`) defines *how an operator gets onto it and tunes it*. **Never double-track paths across the two repos** — `mios.git` owns the FHS overlay (including `usr/share/mios/ai/`), Containerfile, systemd units, Quadlets, kernel args, tmpfiles, sysusers; `mios-bootstrap.git` owns installer scripts and the user profile layer.

> Note: several top-level scripts here (`Get-MiOS.ps1`, `build-mios.*`, `bootstrap.*`, `install.*`, `mios.toml`) are bootstrap-owned files that also appear in this working tree. The canonical Linux build surface for *this* repo is the `justfile` + `Containerfile` + `automation/`; treat those as the source of truth.

## The build pipeline (the core mental model)

The image is built by a single `Containerfile` that runs **every script in `automation/NN-*.sh` in numeric order**. Each script does one thing (install packages, configure SELinux, render the UKI, generate Quadlets, generate CDI specs…); the numeric prefix encodes execution order. To add a build step, drop a new `45-myfeature.sh` next to its peers — do not thread it through some central dispatcher.

```
Containerfile  →  automation/01-*.sh … 99-postcheck.sh  →  OCI image  →  bootc lifecycle on host
```

Shared build helpers live in `automation/lib/` (`common.sh`, `packages.sh`, `paths.sh`, `globals.sh`, `masking.sh`). `automation/99-postcheck.sh` is the final gate that enforces the architectural laws.

## `mios.toml` — the singular SSOT

`usr/share/mios/mios.toml` is the runtime source of truth for every operator-tunable value: packages, ports, AI lanes, models, services. Packages resolve from `[packages.<section>].pkgs`, parsed by `automation/lib/packages.sh`. **A hardcoded constant that belongs in the TOML is a bug** — lift it into `mios.toml`, expose it in the configurator (`/usr/share/mios/configurator/`), and read it back through the layered overlay.

Three-layer resolution (highest wins): `~/.config/mios/mios.toml` (per-user) → `/etc/mios/…` (host/admin) → `/usr/share/mios/mios.toml` (vendor default). Empty strings do **not** override non-empty values below them.

### Root dotfolder workflow

Root dotfolders are the repository control plane described in
[`.mios/README.md`](.mios/README.md), not replacements for the FHS overlay:
`.prompts/` holds prompt organization, `.mios/system-prompt.md` provides
auth-separated copy/paste blocks for external research/chat applications,
`.dotfiles/` documents the
`mios-bootstrap.git`-owned non-secret operator dotfiles layer,
`.research/` holds research staging,
`.docs/` holds documentation staging, and `.secrets/` is a tracked README-only
boundary whose contents are ignored. Deployable prompt and documentation
artifacts remain under `usr/share/mios/prompts/`, `docs/`, and
`usr/share/doc/mios/` according to their existing ownership and projection
rules. Generated `.artifacts/` and scratch `.work/` content must never be
committed.

## Common commands (`just` is the Linux SSOT)

The `justfile` is the source of truth for the Linux build; `just --list` shows every target.

```bash
just preflight        # host readiness checks (mios-probe, thresholds from [preflight])
just build            # podman build the OCI image (runs preflight + flight-status first)
just lint             # bootc container lint against the freshly built image
just drift-gate       # source-tree fitness functions — NO built image needed; safe on every PR
just iso              # build + emit installer ISO   (also: raw / qcow2 / vhdx / wsl2)
just all              # build + every artifact format
just init-user-space  # seed ~/.config/mios/mios.toml from the vendor template
just show-env         # print resolved MIOS_* environment
just edit             # open the user mios.toml in $EDITOR
```

Windows equivalent of the Linux build: `mios-build-local.ps1`. On a provisioned Windows host the operator drives builds through the `mios` verb dispatcher (`mios build`, `mios dev`), which hands off into the `MiOS-DEV` podman machine — **all builds run inside `MiOS-DEV`, never on the Windows host directly.**

### Tests / lint (run these before proposing a build)

`just drift-gate` is the fast pre-image check and bundles the two test suites:
1. **SSOT-render conformance** — `bash automation/97-ssot-lint.sh` (asserts every `${MIOS_*}` Quadlet placeholder is wired on both ends).
2. **agent-pipe unit tests** — `test_mios_*.py` under `usr/lib/mios/agent-pipe/`, run via `python3` (prefers the `.venv` at `/usr/lib/mios/agents/.venv/bin/python3` if present).

Run a single agent-pipe test directly:
```bash
cd usr/lib/mios/agent-pipe && python3 test_mios_<name>.py
```
Additional standalone dispatcher/extraction tests live in `tests/` (`test-*.py`, `test-*.sh`) — run each file directly.

## The sixteen architectural laws (v0.3.0 — enforced by `automation/98-drift-checks.sh` offline + `automation/99-postcheck.sh` at bake + `bootc container lint`)

Every change must obey these; a failing law fails the build. The law list is
itself an SSOT: `usr/share/mios/mios.toml [laws]` is the canonical registry
(id/slug/`applies_to`/`enforced_by`), and the Law-6 root exceptions live in
`[security.privileged_quadlets]` — never hand-maintain a second copy.

1. **USR-OVER-ETC** — vendor/static config lives under `/usr`: `/usr/lib/<component>.d/`, the `/usr/share/mios/mios.toml` SSOT monolith, and `/usr/lib/mios/mios.d/*.toml` vendor fragments. `/etc` is admin-override-only. The overlay resolves vendor(`/usr`) < host(`/etc`) < user(`~/.config`).
2. **NO-MKDIR-IN-VAR** — every `/var/` path is declared via `usr/lib/tmpfiles.d/*.conf`, never `mkdir`'d in a numbered image-build step.
3. **BOUND-IMAGES** — every Quadlet image is symlinked into `/usr/lib/bootc/bound-images.d/` (generated at bake by `01-system-files-overlay.sh`) so it ships *with* the host.
4. **BOOTC-CONTAINER-LINT** — the **final** instruction of every `Containerfile` is `RUN bootc container lint`; fail the lint, fail the build.
5. **UNIFIED-AI-REDIRECTS** — every agent and tool targets `MIOS_AI_ENDPOINT`. No vendor cloud URLs, no vendor-specific agent/product names, no retired local-lane ports in code, docs, or commits (registry: `[docs].retired_ports`).
6. **UNPRIVILEGED-QUADLETS** — every Quadlet declares `User=`, `Group=`, `Delegate=yes`. Root (`User=root`/`0`) is allowed **only** for units in `mios.toml [security.privileged_quadlets].root`, each with a justification.
7. **NO-HARDCODE** — nothing operator-tunable (ports, model names, scoring params, IPs) is a literal; values resolve through the `mios.toml` cascade. No dates/timestamps baked into comments/docstrings.
8. **SSOT-PROJECTION** — any file *derived* from `mios.toml` is emitted by a generator and guarded by a regenerate-and-diff drift-check; a hand-edited derived surface is a build failure.
9. **ONE-CANONICAL-NAME** — each `MIOS_*` value carries exactly one canonical name, and every `MIOS_*` a consumer references is emitted by the resolver (referenced ⊆ emitted; `automation/lib/mios_var_closure.py`).
10. **BARE-SAFE-ENV** — `install.env` is bare `KEY=value` only, safe under all three parsers (systemd `EnvironmentFile=`, bash `source`, podman `--env-file`): no quotes/whitespace/`$`/backtick/`#` in values.
11. **SECRETS-NEVER-IN-ENV** — password hashes/tokens live only in `/etc/shadow` + `/etc/mios/secrets.env` (0600); never in a group/world-readable env file.
12. **BAKE-NOT-FETCH** — the image bakes runtime payloads (venv, GGUFs, weights); firstboot seeds from the OCI image and **degrades open** — never blocks boot on egress.
13. **NATIVE-DROPINS** — layered config resolves as `*.d` fragments with **tier-major** precedence (vendor `/usr/lib/mios/mios.d` < host `/etc/mios/mios.d` < user); a vendor fragment can never outrank a higher tier. The Python (`mios_toml.py`) and bash (`userenv.sh` twins) resolvers must agree.
14. **TARGET-LANGUAGES** — all NEW applicable code uses the roadmap's language-per-domain targets (Rust for native tooling/services/validation; Python for the AI plane; Bun/TS for the web Portal; bash is thin GLUE only). No new C#/Batch/Go/PowerShell-as-program — those are grandfathered-for-port, not a licence for more (ADR-0011 §2 / WS-LANG).
15. **DOUBLE-REPO-TRIPLE-CHECK** — before ANY change: (1) **double-check BOTH repos** — `mios.git` (this repo; `.git` IS `/`) **and** `mios-bootstrap.git` (the installer/user-overlay) — for current state, the shared cross-repo SSOT (`mios.toml`, the `userenv` twins, `[ports]`/`[colors]`), duplication, and the other agent's in-flight work; (2) **triple-check everything** — re-read the target, re-verify the assumption, and render/parse/test the result — **before acting**. Measure thrice, cut once; **then code**. A surface mirrored across both repos must be updated in both (or the divergence justified).
16. **ONE-TEMPLATE-PER-TYPE** — every supported file type has exactly one canonical template under `usr/share/mios/templates/` declared in SSOT (`[templates.<type>]`), validated by `check_template_conformance`, `check_templates_compilation`, and `check_template_self_conformance`. Scaffolding is performed strictly via `mios new <type>` / `miosd scaffold`.

Quadlets are generated, not hand-edited into final form — see `automation/14-generate-quadlets.sh` and `34-render-quadlets.sh`. Kernel args ship in `usr/lib/bootc/kargs.d/`.

## The local AI plane

The "self-hosted agent OS" half ships *inside* the image and is reachable through one OpenAI-compatible front door named by **`MIOS_AI_ENDPOINT`**. Lanes are named by *function*, not upstream tool:

| Component | `[ports]` key | Role |
|---|---|---|
| `mios-llm-light` | `llm_light` | **Primary** lane — `llama.cpp` behind the `llama-swap` proxy; auto-swaps chat/reasoning models, serves embeddings (`nomic-embed-text`) + the `mios-opencode` coder model. Model map: `usr/share/mios/llamacpp/mios-llm-light.yaml` |
| `mios-llm-heavy` / `-alt` | `vllm` / `sglang` | Heavy GPU lanes (vLLM / SGLang) — **gated off by default** on VRAM grounds |
| agent-pipe | `agent_pipe` | Router/dispatch gateway every front-end talks to; decomposes + fans out to agents, calls tools |
| MiOS-Hermes | `hermes` | OpenAI-compatible agent gateway — owns sessions, the tool-loop, skills, browser control |
| prefilter | `prefilter` | Injects fan-out hints on decomposable prompts |
| `mios-pgvector` | `pgvector` | Unified agent datastore (PostgreSQL + pgvector): memory, events, tool calls, sessions, skills, and a `knowledge` table with vector recall |
| SearXNG | `searxng` | Local search backing `web_search` |
| opencode-gateway | `opencode_gateway` | Serves the coder peer as a `/v1` council member |

Throughline: **inference lanes → agent-pipe/Hermes orchestration → pgvector memory → MCP/A2A tools**. The full contract is `usr/share/doc/mios/reference/api.md`; the agent-facing architectural contract is `usr/share/mios/ai/INDEX.md`.

## Claude Code deltas


- **cwd:** on a deployed host `/` IS both the repo root and the system root. Bootstrap/dev files map directly to FHS destinations. Don't treat `/` as inherently dangerous.
- **Confirm before:** `git push`, `bootc switch`, `bootc upgrade`, `dnf install`, `systemctl`, `rm -rf`, `git reset --hard`, `git clean -fd`, `wsl --unregister`, `podman machine rm`, `Remove-Partition`.
- **Deliverables:** complete replacement files only — no diffs, no `# ... rest unchanged ...` placeholders.
- **Memory:** `/var/lib/mios/ai/memory/` · **Scratch:** `/var/lib/mios/ai/scratch/` (both runtime-only, not committed).
- The repo-root agent files (`MiOS.md`, `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`) are **baked into the image** (`Containerfile` copies them to `/ctx/rootmd/`) and are the deployed agent identity contract — editing this file edits a shipped OS artifact.

## The Blade owns the hardware (load-bearing invariant)

A MiOS-Metal (formerly Mini) Blade is *bare metal*: it owns the NICs, radios, TPM, boot chain and dGPUs, and hosts the MiOS OCI image as a **NIC-less guest** obfuscated from all of it. A fleet is 2-6 Blades, and **every Blade is its own AP forming one mesh Wi-Fi**, as well as a member of the **HCI mesh VPN cluster** the hyper-converged lanes (Ceph, k3s, Pacemaker) run over. **No hosted node is ever an access point** -- hardware-facing roles live on the Blade and must be *unclaimable* by a guest archetype, because a capability the guest plane can express but can never serve reads as available and fails at the hardware boundary. Three role shapes follow: **universal-per-Blade** (radio, mesh membership), **singleton-across-Blades** (WAN gateway, mesh coordinator), **guest-plane** (k3s, Pacemaker). Detail: `mios.git` TASKS.md task_929..task_935.

## Global code form

Three standing operator directives, all scoped globally. They constrain *how*
code is written here, not what it does.

- **Rust static binaries, globally.** MiOS runs from a refined, canned,
  minified code base compiled to Rust static binaries. Law 14
  (TARGET-LANGUAGES) is the floor -- it grandfathers what exists -- and this
  is the destination: all of it converts. Prefer a Rust static binary in
  `tools/native/` over another Python or bash script for any new generator,
  gate, verb backend or service.
- **"Canned" means hardened templates.** Code and scripts are kept as hardened
  templates an AI generates the next tool *from*. That is Law 16
  (ONE-TEMPLATE-PER-TYPE) read generatively: one canonical template per file
  type under `usr/share/mios/templates/`, declared in `[templates.<type>]`,
  scaffolded strictly via `mios new <type>` / `miosd scaffold`. Do not
  hand-roll a file of a type that already has a template.
- **OpenAI-format schemas, globally.** Every schema is an OpenAI format --
  not only the `/v1` wire surface but schemas everywhere.

## Conventions

- **Latest packages** — default to newest stable upstream when pinning RPMs, OCI tags, or base images; bump conservative pins forward on next touch unless held for a documented reason.
- **OpenAI-API-only** — the OpenAI public surface (`/v1/chat/completions`, `/v1/responses`, `/v1/embeddings`, `/v1/models`, function-calling, MCP via Responses API) is the only addressable AI contract.
- **Every artifact is tracked** — generated files get a `.gitignore` whitelist line, staged and committed; `git pull` must restore full context.
- **Persistence sanitization** — before writing to memory/scratch, strip vendor-specific names and chat metadata, reduce paths to FHS canonicals, never persist secrets.

See `CONTRIBUTING.md` for contribution conventions and `usr/share/doc/mios/guides/engineering.md` for the full build-pipeline + shell rules.
