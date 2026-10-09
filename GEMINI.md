<!-- AI-hint: Per-tool entry stub for the Gemini CLI on mios.git (the system FHS overlay + OCI/bootc image source of MiOS). Defers all agent identity to the canonical agent prompt (AGENTS.md -> /usr/share/mios/ai/system.md) and records only the Gemini-CLI delta: layered prompt loading order and the binding to the single OpenAI-compatible AI endpoint (MIOS_AI_ENDPOINT) per Architectural Law 5. The endpoint fronts the local mios-llm-light inference lane, the agent-pipe/MiOS-Hermes orchestration, and PostgreSQL+pgvector memory.
     AI-related: AGENTS.md, /usr/share/mios/ai/system.md, /etc/mios/ai/system-prompt.md, ~/.config/mios/system-prompt.md, MIOS_AI_ENDPOINT, usr/share/mios/llamacpp/mios-llm-light.yaml, mios-llm-light, mios-agent-pipe, mios-pgvector -->
# GEMINI.md

> _`GEMINI.md` — per-tool stub for the **Gemini CLI** on `mios.git`.
> The canonical agent entry point for this repo is **[`AGENTS.md`](AGENTS.md)**
> (the [agents.md][1] standard), which defers to the deployed vendor canonical
> **`/usr/share/mios/ai/system.md`**. This stub does NOT re-state the agent
> identity, posture, or tool-loop — it records ONLY the Gemini-CLI loading order
> and the endpoint binding. No hardcoded topics, apps, or keywords._
>
> [1]: https://agents.md

## What this is, and where it fits

MiOS is an immutable, `bootc`/OCI-shaped Fedora workstation that is also a local,
self-replicating **agentic AI OS**: one OCI image boots the desktop, upgrades
like a `git pull`, rolls back like a Ctrl-Z, and ships a full agent stack baked
in. Every agent on the host — whether it arrives as Open WebUI, the `mios` CLI,
Claude Code, or the **Gemini CLI** — is just another node speaking to **one local
brain** over **one OpenAI-API-compatible endpoint**. There is no vendor lock-in
and no per-tool "API key to the cloud": the editor/CLI you happen to use is
interchangeable plumbing in front of the same inference lanes, agent
orchestration, and memory.

This repo (`mios.git`) is the **system FHS overlay and OCI image source**, dual-structured as both a **Linux FHS compliant** root filesystem and an **AI training image shape / corpus dataset** —
`usr/`, `etc/`, `srv/`, `var/`, Containerfile, systemd units, Quadlets, and
Architectural Laws. (The sibling `mios-bootstrap.git` defines the installer
and user-editable profile overlay.) This file exists so the Gemini CLI plugs
into the running system correctly. It carries only the **Gemini-CLI delta** —
how to load the shared identity and which endpoint to bind. Everything
substantive lives once in the canonical agent prompt and is shared by every agent.

### Five Load-Bearing Architectural Invariants
Ensure you reason about the system substrate using the following four corrected invariants:
1. **`/var` Persists by Default**: On bootc/ostree systems, `/var` is a persistent location rather than a volatile tmpfs. This guarantees continuity for large models, database state, and virtual machine disks.
2. **Unified Kernel Image (UKI) vs MOK Conflation**: The bootloader and kernel signing chain is a Unified Kernel Image (`shim -> systemd-boot -> signed UKI`) where kernel command line parameters (kargs) are baked and signed into the UKI itself. This is distinct from Machine Owner Key (MOK) module signing, which only governs out-of-tree runtime driver signature checks.
3. **Graphics Virtualization (venus vs CUDA)**: The `venus` VirtIO GPU protocol is strictly a graphics/Vulkan transport. It does not support CUDA execution inside a microVM; running CUDA workloads inside a virtualized guest requires whole-device VFIO hardware passthrough.
4. **GPU Fractioning / mediated vGPU Limit**: GPU fractioning (using `mdevctl` or SR-IOV) is technically impossible without loading a physical host-side PF driver. Therefore, on a driver-free host, the only supported option is whole-device passthrough via `vfio-pci`; vGPU fractioning requires an explicit, invariant-breaking opt-in.
5. **The Blade owns the hardware; the MiOS image is a guest obfuscated from it.** A MiOS-Metal (formerly Mini) Blade is *bare metal*: it owns the NICs, radios, TPM, boot chain and dGPUs, and hosts the MiOS OCI image as a **NIC-less guest** obfuscated from all of it. A fleet is 2-6 Blades, and **every Blade is its own AP forming one mesh Wi-Fi**, as well as a member of the **HCI mesh VPN cluster** the hyper-converged lanes (Ceph, k3s, Pacemaker) run over. **No hosted node is ever an access point** -- hardware-facing roles live on the Blade and must be *unclaimable* by a guest archetype. Three role shapes follow: **universal-per-Blade** (radio, mesh membership), **singleton-across-Blades** (WAN gateway, mesh coordinator), **guest-plane** (k3s, Pacemaker). See `TASKS.md` task_929..task_935.

## Role and Objective

The Gemini CLI runs as a **MiOS agent** — one node in the local MiOS AIOS, not a
standalone Google chatbot. **Operate under the canonical agent prompt** — the
single system identity for every agent on this host. Load it and behave exactly
as it specifies; everything below is the Gemini-CLI delta only.

## Loading order

Identity is a three-layer SSOT, mirroring the `mios.toml`/`profile.toml` override
model (**highest layer wins**). Load the vendor canonical first, then apply each
override layer if present:

1. Load `/usr/share/mios/ai/system.md` (vendor canonical, shipped in the image).
2. Apply `/etc/mios/ai/system-prompt.md` if present (host/admin override, deployed
   by bootstrap).
3. Apply `~/.config/mios/system-prompt.md` if present (per-user override, seeded
   from `etc/skel/` on first login).

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

## Endpoint binding (Architectural Law 5)

The Gemini CLI routes through the **same OpenAI-API-compatible endpoint as every
other MiOS agent** — `MIOS_AI_ENDPOINT` —
per **Architectural Law 5 (UNIFIED-AI-REDIRECTS)**: no agent or tool may hardcode
a vendor URL or port; they all resolve this one endpoint. No
`gemini.googleapis.com` endpoints; no proprietary protocols — function-calling
and the tool-calling loop go through the OpenAI-compatible surface.

What sits behind that endpoint is the MiOS inference and orchestration stack, not
a cloud:

- **Inference** — the everyday lane is **`mios-llm-light`**, the primary local
  LLM engine (llama.cpp behind the upstream `ghcr.io/mostlygeek/llama-swap` proxy
  image) on the `llm_light` port. It auto-swaps GGUF chat/reasoning models behind one
  endpoint, KV-pages each conversation, serves embeddings (`nomic-embed-text` via
  OpenAI-compatible `/v1/embeddings`), and hosts the `mios-opencode` coder model —
  config in
  [`usr/share/mios/llamacpp/mios-llm-light.yaml`](usr/share/mios/llamacpp/mios-llm-light.yaml).
  Heavy work, when enabled, falls to the GPU lanes **`mios-llm-heavy`** (vLLM,
  port key `vllm`) and **`mios-llm-heavy-alt`** (SGLang, port key `sglang`), both gated/off-by-default on
  VRAM. These speak the OpenAI/Ollama-compatible API, so any OpenAI-API client
  talks to them unchanged — but the *engine* is `llama.cpp`/SGLang/vLLM, not a
  hosted service.
- **Orchestration** — requests flow through the MiOS agent pipeline: the
  **agent-pipe** router/dispatch gateway and the **MiOS-Hermes** OpenAI-compatible
  agent gateway, which inject the canonical identity per request (it is not baked
  into any model), run the tool-calling loop over the unified **MCP**
  tool/skill/recipe surface, and delegate across peer agents over **A2A**.
- **Memory** — the unified agent datastore is **PostgreSQL + pgvector**
  (`mios-pgvector`), holding agent memory, events, tool calls, sessions, skills,
  scratch, and a `knowledge` table of finished Q+A with vector recall (embeddings
  from `nomic-embed-text` on `mios-llm-light`).

So the Gemini CLI's job is narrow and clear: **bind to `MIOS_AI_ENDPOINT` only**,
load the shared identity, and let the MiOS stack behind that one endpoint —
inference lanes → agent-pipe/Hermes orchestration → pgvector memory → MCP/A2A —
do the rest.
