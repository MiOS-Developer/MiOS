<!-- AI-hint: Defines the MiOS local agentic-AI pipeline end-to-end — how a request flows from a front-end (OWUI / Discord / mios CLI) through the agent-pipe orchestrator, the MiOS-Hermes tool-loop gateway, and the function-named inference lanes (mios-llm-light primary, gated heavy lanes) into pgvector memory and MCP/A2A tool/agent surfaces. Use this to understand how the AI plane is wired, why, and what serves what.
     AI-related: /usr/lib/mios/agent-pipe/server.py, /usr/share/mios/ai/system.md, /usr/share/mios/ai/INDEX.md, /usr/share/mios/llamacpp/mios-llm-light.yaml, /usr/share/mios/postgres/schema-init.sql, /usr/share/mios/mios.toml, /etc/mios/mios.toml, mios-agent-pipe, mios-delegation-prefilter, mios-llm-light, mios-pgvector, mios-opencode-gateway -->
# MiOS AI Architecture

**Audience:** operators and contributors wiring or auditing the MiOS agent
plane. **Purpose:** a single, current-state description of how MiOS's local
agentic-AI brain is wired end-to-end — what is in the image vs. what an operator
must still turn on — and *why* each piece exists.

## Where this fits in the whole system

MiOS is one thing built two ways at once: an **immutable, bootc/OCI-shaped
Fedora workstation** (the whole OS is a single rebuildable container image —
boot it, `bootc upgrade` it like a `git pull`, `bootc rollback` it like a
Ctrl-Z) that is *also* a **local, self-hosted, self-replicating agentic AI
operating system**. The same image that ships GNOME/Wayland, GPU-via-CDI,
KVM/libvirt passthrough, and a k3s+Ceph cluster path also ships the full agent
stack described here.

This document covers that second half — the AI plane. It is reachable through a
single OpenAI-compatible endpoint named by `MIOS_AI_ENDPOINT`; every agent, tool, and editor on the box resolves
to that one endpoint (Architectural Law 5). Because the stack is baked into the
same immutable image as the OS, it is version-locked to the OS and reproduced
exactly on every host that pulls the ref — that is what makes a *local* agent
runtime trustworthy rather than a pile of pip-installed daemons to babysit.

The throughline of this doc: **front-end → agent-pipe orchestration → Hermes
tool-loop → function-named inference lanes → pgvector memory → MCP/A2A tools &
agents.**

## Live chain

```
operator front-end          (Open WebUI, Discord/chat gateway, or the `mios` CLI)
      │
      ▼
  agent-pipe (port key agent_pipe)           (mios-agent-pipe.service)
      │  standalone orchestrator: router + REFINE + council/swarm fan-out
      │  + CRITIC/POLISH; injects the refined plan into every sub-agent hop;
      │  fronts Hermes for every gateway; writes/reads pgvector memory
      │
      ▼
  prefilter (port key prefilter)            (mios-delegation-prefilter.service)
      │  injects tool_choice=delegate_task on fan-outable prompts,
      │  forwards to Hermes
      │
      ▼
  Hermes (port key hermes)               (hermes-agent.service)
      │  ├─ OpenAI-compatible agent gateway: sessions, the tool-loop, skills,
      │  │   browser/CDP control
      │  ├─ skills: mios-environment, windows-control, pc-control,
      │  │           parallel-fanout, self-improvement, ...
      │  ├─ tools: terminal, file, web, delegation, skills, memory, todo,
      │  │           session_search, code_execution, browser, discord,
      │  │           cronjob, clarify
      │  ├─ delegate_task → child agents
      │  └─ dispatch → MiOS-OpenCoder (opencode /v1 council peer, port key opencode_gateway)
      │
      ▼
  inference lanes            (function-named; OpenAI/Ollama-compatible API)
      │    mios-llm-light (port key llm_light)   PRIMARY — llama.cpp behind mios-llm-light;
      │                            everyday chat/reasoning models + the
      │                            mios-opencode coder model + embeddings
      │                            (nomic-embed-text, /v1/embeddings)
      │    mios-llm-heavy (port key vllm)   heavy GPU lane (vLLM, served-name
      │                            mios-heavy); gated off by default (VRAM)
      │    mios-llm-heavy-alt (port key sglang)  alternate heavy lane (SGLang); gated
      │
      ▼
  response streams back through the orchestrator to the front-end
```

> **Inference is function-named, not tool-named.** The lanes are
> `mios-llm-light` / `mios-llm-heavy` / `mios-llm-heavy-alt`, named by what they
> *do*. `mios-llm-light` (the upstream proxy image
> `ghcr.io/mostlygeek/llama-swap`) and the Ollama-compatible API are legitimate
> *upstream* references — the engines speak that API so any OpenAI-API client
> talks to them unchanged. The earlier Ollama backend, the legacy datastore datastore, and
> Qdrant vector store have been **removed**; inference + embeddings now run on
> `mios-llm-light` and the unified agent datastore is **PostgreSQL + pgvector**.

## The inference lanes (what serves what)

| Lane | Unit | `[ports]` key | Role |
|---|---|---|---|
| MiOS-LLM-Light | `mios-llm-light.service` | `llm_light` | **Primary** local inference — `llama.cpp` behind the `mios-llm-light` proxy image; multi-model auto-swap + KV-cache paging (slot save/restore to disk); serves everyday chat/reasoning models, the `mios-opencode` coder model, **and** embeddings (`nomic-embed-text`, OpenAI-compat `/v1/embeddings`). |
| MiOS-LLM-Heavy | `mios-llm-heavy.service` | `vllm` | Heavy GPU lane (vLLM, served-name `mios-heavy`, PagedAttention + prefix cache). **Gated/off-by-default** (VRAM). |
| MiOS-LLM-Heavy-Alt | `mios-llm-heavy-alt.service` | `sglang` | Alternate heavy lane (SGLang, HiCache CPU KV-offload). **Gated/off-by-default** (VRAM). |
| MiOS-LLM-Worker | `mios-llm-worker@.service` | — | Single-model swarm workers (templated; for the dGPU swarm topology). |

The light lane's model map is
[`usr/share/mios/llamacpp/mios-llm-light.yaml`](../../llamacpp/mios-llm-light.yaml).
Each chat model runs `--parallel 1 --slot-save-path` so it lands on one
deterministic slot and the agent-pipe's KV-paging (`POST /slots/{id}?action=
save|restore`) can checkpoint/restore a conversation's KV to disk — the AIOS
Context Manager, fleet-wide. The embed model runs an `--embedding` server so
`/v1/embeddings` is served locally. The reasoning model the pipeline resolves to
is set by `[ai].model` (e.g. `granite4.1:8b`); `mios-llm-light` aliases the
legacy/role model names the pipeline still emits onto the served GGUF so the
lane is a drop-in.

The heavy lanes (`mios-llm-heavy`, `mios-llm-heavy-alt`) are gated in
`mios.toml` and stay inert until enabled and reachable (`health_gate`).

## Background daemons (READ-only — micro-LLM for status, never launch)

These observe; they never trigger actions. Operator clarification (2026-05-16):
"MiOS-Hermes launches and operates things themselves — just the micro-LLMs read
logs and files on pass/fail." Daemons stay observation-only.

| daemon | role |
|---|---|
| mios-log-watcher | classify journal events |
| mios-cron-director | gate cron rules on system state |
| mios-agent-nudger | detect refusal patterns in agent output |
| mios-micro-llm | CLI wrapper for direct classification |

The always-on `mios-daemon-agent` (a CPU-pinned, low-resource sub-agent) tails
system logs and feeds context to the council; per `[agents.mios-daemon-agent]`
in `mios.toml` it now dispatches reasoning to the heavy SGLang lane (port key `sglang`)
like the other council agents rather than the contended light lane. Its
background log/journal watch stays read-only.

## Memory — PostgreSQL + pgvector

The unified agent datastore is the **`mios-pgvector`** container
(`mios-pgvector.service`, `:5432`), accessed via the pure-python `mios-pg-query`
client and `mios-db --pg`. Its tables (in
`usr/share/mios/postgres/schema-init.sql`) include `agent_memory`, `event`,
`tool_call`, `session`, `skill`, `scratch`, `knowledge`, `sys_env`, `kanban`,
`directory_entry`, `person`, and `agent_keypair`.

The `knowledge` table stores finished Q+A with vector recall: answers are
embedded at write time and recalled by cosine similarity (threshold-gated) into
the agent's system prefix on later turns. The embeddings come from
`nomic-embed-text`, served by `mios-llm-light` — so the memory plane and the
inference plane share one local embedding lane and no external service is in the
loop.

## Tools & federation — MCP / A2A / opencode

- Agents call **tools over MCP** (the universal MiOS verb/skill/recipe surface)
  and reach **other agents over A2A**. MiOS both *publishes* and *consumes*
  these surfaces, so peer agents can be federated by adding them to the
  `mcp.json` / `a2a-peers.json` overlays.
- `web_search` is backed by a local **SearXNG** (`mios-searxng.service`,
  port key `searxng`); the front-end is **Open WebUI** (`mios-open-webui.service`, port key `open_webui`).
- The coder peer is served through the **opencode-gateway**
  (`mios-opencode-gateway.service`, port key `opencode_gateway`) as a first-class OpenAI `/v1`
  council member, registered as `[agents.opencode]`:
  ```
  POST http://localhost:${MIOS_PORTS_OPENCODE_GATEWAY}/v1/chat/completions
    { "model": "mios-opencode:latest", "messages": [...] }
  ```
  The agent-pipe orchestrator dispatches code-heavy facets to it in parallel
  with the primary. OpenCoder uses ONLY offline local models (it targets
  `mios-llm-light`). Its strengths: filesystem navigation, multi-file edits,
  PC-control task chains, and long-running coding work. It is currently
  `default=false`/`fanout=false` in `mios.toml` because `opencode run`
  headless invocation hangs (see `[agents.opencode]` rationale); re-enable once
  that is fixed.

## Single-source-of-truth pointers

- `mios.toml [ai]` — model identities + the OpenAI-compat endpoint
  (`MIOS_AI_ENDPOINT`, Law 5).
- `mios.toml [ai.host_thresholds]` — auto-pick model by RAM (big/mid/small);
  `micro_model` + `sys_agent_model`.
- `mios.toml [agents.*]` — the council/swarm registry: each agent's
  `endpoint` (OpenAI-compat `/v1`), `model`, `role`, and `strengths`; the
  refine pass picks targets from this list.
- `usr/share/mios/llamacpp/mios-llm-light.yaml` — the light-lane model map (served
  GGUFs, aliases, KV-paging slots, embeddings).
- `usr/share/mios/postgres/schema-init.sql` — the pgvector schema (the agent
  datastore tables above).
- `usr/share/mios/ai/system.md` — canonical agent system prompt / persona +
  truthfulness rules.
- `usr/share/mios/ai/INDEX.md` — the architectural contract (agent-facing:
  the laws + the OpenAI-compatible API surface).
- `usr/share/mios/hermes/skills/*/SKILL.md` — capability index.

## Principles (operator-directed)

### No hardcoded paths or words

Operator directive (2026-05-16): "I DON'T WANT ANY HARDCODED PATHS OR WORDS!!!
SKILLS AND TOOLS ARE TEMPLATES FOR ANY VARIABLES!!!".

Every path / executable name / threshold / pattern that MiOS helpers consume
reads from `mios.toml` (layered: `~/.config/mios/mios.toml` <
`/etc/mios/mios.toml` < `/usr/share/mios/mios.toml`, highest wins). Operators
tune via TOML, not script edits. There are no hardcoded topic, app, or keyword
deny-lists anywhere in the routing path — the model plus the current context
route everything.

Currently TOML-driven:
- `[paths].everything_cli` (es.exe probe list)
- `[paths].powershell_exe` + `[paths].cmd_exe` (Windows tools)
- `[paths].launcher_socket` (broker)
- `[appearance].gtk_theme` + `[appearance].cursor_*` + `[appearance].adw_color_scheme`
- `[ai].endpoint` + `[ai.host_thresholds].micro_model` etc.
- `/usr/share/mios/ai/refusal-patterns.txt` (operator-extensible regex list)

Intentionally fixed (not lifted to TOML): service-unit `ExecStart=` paths
(systemd unit syntax) and any remaining SKILL.md path mentions, which should
read "the configured X" rather than a literal.

### Use the agent's NATIVE tools + skills, don't shadow

Operator directive (2026-05-16): "Hermes-Agent NATIVE TOOLS AND SKILLS".

Hermes ships a rich native toolset (`skill_view`, `skill_manage`,
`memory_save`, `memory_search`, `delegate_task`, `clarify`, `terminal`, …).
MiOS-overlay SKILL.md files should be MINIMAL — they exist to inform the agent
about the host's MiOS-specific surface (helpers like `mios-find` /
`mios-windows`), NOT to substitute for native behaviour.

Stop GROWING SOUL.md with "lesson learned" text-blocks each time the agent
fails. Instead:
- Let the agent `memory_save` corrections itself (native self-learning loop,
  now persisted in pgvector).
- Reserve SOUL.md for the truthfulness principles + the MiOS-specific surface
  map.
- Keep SKILL.md scoped to "here is the MiOS environment; here are the helpers"
  — not "here are the 47 phrases you mustn't say."
- Use OWUI Filter Functions for post-hoc cleanup, not model-prompt rules.

The front-end's role is to GIVE the operator's MiOS environment to the agent;
the agent's native learning + skill mechanisms then do the work.

### Every sub-agent hop carries the refined plan

The agent-pipe injects the refined plan as a system-message prefix on every
front-end → sub-agent hop; no sub-agent runs on raw user text. This keeps the
council/swarm coherent and is why the orchestrator (not the prefilter) owns
refine/critic/polish.

## History (superseded snapshot)

This file began as a 2026-05-16 snapshot describing an Ollama-backed chain in
which the prefilter did the refinement and `mistral-magistral-small-2509` ran on the retired
Ollama lane under a tight VRAM budget. That topology has since changed:

- Inference moved off **Ollama** to the function-named lanes
  (`mios-llm-light` primary on the `llm_light` port, gated heavy lanes); Ollama survives
  only as an upstream API-compat reference and in migration notes.
- The datastore moved off **the legacy datastore**/**Qdrant** to **PostgreSQL + pgvector**.
- The **agent-pipe** (port key `agent_pipe`) became the standalone orchestrator that owns
  refine/council/swarm/critic/polish; the prefilter (port key `prefilter`) was narrowed to
  fan-out hint injection.
- **opencode** became a co-equal `/v1` council peer via the opencode-gateway,
  not a Hermes ACP-over-stdio child.

The model/VRAM trade-off the snapshot debated (a smaller co-resident reasoner
vs. eviction churn) was resolved in favour of the auto-swapping light lane plus
gated heavy lanes; the open "which exact Gemma 4 tag" question resolved to the
`[ai].model` setting the light lane serves.
